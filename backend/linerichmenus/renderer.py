from __future__ import annotations

import struct
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .apps import FONT_PATH, runtime_prerequisites_available
from .catalog import DefaultTemplateCatalog
from .types import (
    InputFieldError,
    NormalizedTemplate,
    RenderRejected,
    RenderedImage,
    SafeResultCode,
)


_FONT_SIZE = 28
_PADDING = 48
_LINE_SPACING = 12
_MAX_IMAGE_BYTES = 1024 * 1024
_PALETTE = (
    (24, 88, 132, 255),
    (18, 112, 103, 255),
    (112, 76, 145, 255),
)
_TEXT_COLOR = (255, 255, 255, 255)


class DefaultDeterministicRenderer:
    def __init__(
        self,
        *,
        catalog: DefaultTemplateCatalog | None = None,
        font_path: Path = FONT_PATH,
    ) -> None:
        self._catalog = catalog or DefaultTemplateCatalog()
        self._font_path = font_path

    def render(self, template: NormalizedTemplate) -> RenderedImage | RenderRejected:
        if not runtime_prerequisites_available():
            return _image_rejected()
        descriptor = self._catalog.get(template.reference)
        if descriptor is None or len(template.fields) != len(descriptor.areas):
            return _image_rejected()
        try:
            supported = _font_code_points(self._font_path)
            font = ImageFont.truetype(str(self._font_path), _FONT_SIZE)
        except (OSError, ValueError, struct.error):
            return _image_rejected()

        glyph_errors = []
        for area, field in zip(descriptor.areas, template.fields, strict=True):
            if any(ord(character) not in supported for character in field.display_name):
                glyph_errors.append(
                    InputFieldError(
                        field=f"{area.field_name}.displayName",
                        reason="unsupported_glyph",
                    )
                )
        if glyph_errors:
            return RenderRejected(
                code=SafeResultCode.IMAGE_INVALID,
                errors=tuple(glyph_errors),
            )

        image = Image.new("RGBA", (descriptor.width, descriptor.height))
        draw = ImageDraw.Draw(image)
        for index, (area, field) in enumerate(
            zip(descriptor.areas, template.fields, strict=True)
        ):
            draw.rectangle(
                (area.x, area.y, area.x + area.width - 1, area.y + area.height - 1),
                fill=_PALETTE[index],
            )
            try:
                lines = _wrap_two_lines(
                    field.display_name,
                    font=font,
                    maximum_width=area.width - 2 * _PADDING,
                )
            except ValueError:
                return _image_rejected()
            draw.multiline_text(
                (area.x + area.width / 2, area.y + area.height / 2),
                "\n".join(lines),
                font=font,
                fill=_TEXT_COLOR,
                anchor="mm",
                align="center",
                spacing=_LINE_SPACING,
            )

        rgba_bytes = image.tobytes()
        digest = _pixel_digest(
            template_id=template.reference.template_id,
            version=template.reference.version,
            width=descriptor.width,
            height=descriptor.height,
            rgba_bytes=rgba_bytes,
        )
        binary = _encode_png(image)
        if not _valid_line_png(binary, descriptor.width, descriptor.height):
            return _image_rejected()
        return RenderedImage(
            content_type="image/png",
            width=descriptor.width,
            height=descriptor.height,
            pixel_digest=digest,
            binary=binary,
        )


def _wrap_two_lines(text: str, *, font, maximum_width: int) -> tuple[str, ...]:
    lines = [""]
    for character in text:
        candidate = lines[-1] + character
        if lines[-1] and font.getlength(candidate) > maximum_width and len(lines) == 1:
            lines.append(character)
        else:
            lines[-1] = candidate
    if any(font.getlength(line) > maximum_width for line in lines):
        raise ValueError("normalized display name does not fit fixed layout")
    return tuple(lines)


def _pixel_digest(*, template_id, version, width, height, rgba_bytes) -> str:
    components = (
        template_id.encode("utf-8"),
        str(version).encode("ascii"),
        str(width).encode("ascii"),
        str(height).encode("ascii"),
        rgba_bytes,
    )
    digest = sha256()
    for component in components:
        digest.update(len(component).to_bytes(8, "big"))
        digest.update(component)
    return digest.hexdigest()


def _encode_png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _valid_line_png(binary: bytes, width: int, height: int) -> bool:
    if not binary or len(binary) > _MAX_IMAGE_BYTES:
        return False
    try:
        with Image.open(BytesIO(binary)) as encoded:
            encoded.load()
            return (
                encoded.format == "PNG"
                and encoded.mode == "RGBA"
                and encoded.size == (width, height)
                and 800 <= width <= 2500
                and height >= 250
                and width / height >= 1.45
            )
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        return False


def _image_rejected() -> RenderRejected:
    return RenderRejected(code=SafeResultCode.IMAGE_INVALID)


@lru_cache(maxsize=4)
def _font_code_points(path: Path) -> frozenset[int]:
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError("invalid font")
    table_count = struct.unpack_from(">H", data, 4)[0]
    cmap = None
    for index in range(table_count):
        record = 12 + index * 16
        if record + 16 > len(data):
            raise ValueError("invalid font table")
        tag, offset, length = struct.unpack_from(">4s4xII", data, record)
        if tag == b"cmap":
            cmap = memoryview(data)[offset : offset + length]
            break
    if cmap is None or len(cmap) < 4:
        raise ValueError("font cmap missing")
    subtable_count = struct.unpack_from(">H", cmap, 2)[0]
    offsets = set()
    for index in range(subtable_count):
        record = 4 + index * 8
        if record + 8 > len(cmap):
            raise ValueError("invalid cmap record")
        offsets.add(struct.unpack_from(">I", cmap, record + 4)[0])
    code_points: set[int] = set()
    for offset in offsets:
        if offset + 2 > len(cmap):
            raise ValueError("invalid cmap offset")
        format_number = struct.unpack_from(">H", cmap, offset)[0]
        if format_number == 4:
            code_points.update(_read_cmap_format_4(cmap, offset))
        elif format_number in {12, 13}:
            code_points.update(_read_cmap_format_12(cmap, offset, format_number))
    if not code_points:
        raise ValueError("supported glyph set unavailable")
    return frozenset(code_points)


def _read_cmap_format_4(cmap, offset):
    length = struct.unpack_from(">H", cmap, offset + 2)[0]
    end = offset + length
    if end > len(cmap) or length < 16:
        raise ValueError("invalid cmap format 4")
    segment_count = struct.unpack_from(">H", cmap, offset + 6)[0] // 2
    end_codes = offset + 14
    start_codes = end_codes + segment_count * 2 + 2
    deltas = start_codes + segment_count * 2
    range_offsets = deltas + segment_count * 2
    result = set()
    for index in range(segment_count):
        start = struct.unpack_from(">H", cmap, start_codes + index * 2)[0]
        stop = struct.unpack_from(">H", cmap, end_codes + index * 2)[0]
        delta = struct.unpack_from(">h", cmap, deltas + index * 2)[0]
        range_offset_position = range_offsets + index * 2
        range_offset = struct.unpack_from(">H", cmap, range_offset_position)[0]
        for code_point in range(start, stop + 1):
            if code_point == 0xFFFF:
                continue
            if range_offset == 0:
                glyph = (code_point + delta) & 0xFFFF
            else:
                glyph_position = range_offset_position + range_offset + 2 * (code_point - start)
                if glyph_position + 2 > end:
                    raise ValueError("invalid cmap glyph offset")
                glyph = struct.unpack_from(">H", cmap, glyph_position)[0]
                if glyph:
                    glyph = (glyph + delta) & 0xFFFF
            if glyph:
                result.add(code_point)
    return result


def _read_cmap_format_12(cmap, offset, format_number):
    length = struct.unpack_from(">I", cmap, offset + 4)[0]
    end = offset + length
    if end > len(cmap) or length < 16:
        raise ValueError("invalid cmap format 12")
    group_count = struct.unpack_from(">I", cmap, offset + 12)[0]
    result = set()
    for index in range(group_count):
        position = offset + 16 + index * 12
        if position + 12 > end:
            raise ValueError("invalid cmap group")
        start, stop, glyph = struct.unpack_from(">III", cmap, position)
        for code_point in range(start, stop + 1):
            glyph_id = glyph if format_number == 13 else glyph + code_point - start
            if glyph_id:
                result.add(code_point)
    return result
