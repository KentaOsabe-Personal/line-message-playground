from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from urllib.parse import urlsplit

from .types import (
    InputFieldError,
    InputRejected,
    NormalizedTemplate,
    TemplateArea,
    TemplateDescriptor,
    TemplateFieldValue,
    TemplateInput,
    TemplateReference,
)


_WIDTH = 2500
_HEIGHT = 843


def _areas(widths: tuple[int, ...]) -> tuple[TemplateArea, ...]:
    cursor = 0
    result = []
    for index, width in enumerate(widths, start=1):
        result.append(
            TemplateArea(
                field_name=f"area{index}",
                description=f"左から{index}番目のリンク領域",
                x=cursor,
                y=0,
                width=width,
                height=_HEIGHT,
            )
        )
        cursor += width
    if cursor != _WIDTH:
        raise ValueError("template geometry does not cover canvas")
    return tuple(result)


_TEMPLATES = (
    TemplateDescriptor(
        reference=TemplateReference("jp-link-one", 1),
        display_name="1リンク",
        width=_WIDTH,
        height=_HEIGHT,
        areas=_areas((_WIDTH,)),
        display_name_limit=20,
        uri_limit=1000,
    ),
    TemplateDescriptor(
        reference=TemplateReference("jp-link-two", 1),
        display_name="2リンク",
        width=_WIDTH,
        height=_HEIGHT,
        areas=_areas((1250, 1250)),
        display_name_limit=20,
        uri_limit=1000,
    ),
    TemplateDescriptor(
        reference=TemplateReference("jp-link-three", 1),
        display_name="3リンク",
        width=_WIDTH,
        height=_HEIGHT,
        areas=_areas((834, 833, 833)),
        display_name_limit=20,
        uri_limit=1000,
    ),
)


class DefaultTemplateCatalog:
    def list_templates(self) -> tuple[TemplateDescriptor, ...]:
        return _TEMPLATES

    def get(self, reference: TemplateReference) -> TemplateDescriptor | None:
        for descriptor in _TEMPLATES:
            if descriptor.reference == reference:
                return descriptor
        return None

    def normalize(self, command: TemplateInput) -> NormalizedTemplate | InputRejected:
        descriptor = self.get(command.reference)
        if descriptor is None:
            return InputRejected((InputFieldError("template", "unknown"),))

        errors: list[InputFieldError] = []
        expected = set(descriptor.required_fields)
        supplied = {key for key in command.fields if isinstance(key, str)}
        if len(supplied) != len(command.fields):
            errors.append(InputFieldError("fields", "invalid"))
        for field_name in descriptor.required_fields:
            if field_name not in supplied:
                errors.append(InputFieldError(field_name, "required"))
        for field_name in sorted(supplied - expected):
            errors.append(InputFieldError(str(field_name), "unexpected"))

        normalized: list[TemplateFieldValue] = []
        for field_name in descriptor.required_fields:
            if field_name not in command.fields:
                continue
            value = command.fields[field_name]
            if not isinstance(value, Mapping):
                errors.append(InputFieldError(field_name, "invalid"))
                continue
            allowed_keys = {"displayName", "uri"}
            supplied_keys = {key for key in value if isinstance(key, str)}
            if len(supplied_keys) != len(value):
                errors.append(InputFieldError(field_name, "invalid"))
            for key in sorted(supplied_keys - allowed_keys):
                errors.append(InputFieldError(f"{field_name}.{key}", "unexpected"))
            for key in ("displayName", "uri"):
                if key not in value:
                    errors.append(InputFieldError(f"{field_name}.{key}", "required"))
            display_name = (
                _normalize_display_name(
                    value["displayName"], field_name, descriptor, errors
                )
                if "displayName" in value
                else None
            )
            uri = (
                _normalize_uri(value["uri"], field_name, descriptor, errors)
                if "uri" in value
                else None
            )
            if display_name is not None and uri is not None:
                normalized.append(TemplateFieldValue(display_name=display_name, uri=uri))

        if errors:
            return InputRejected(tuple(errors))
        return NormalizedTemplate(reference=descriptor.reference, fields=tuple(normalized))


def _normalize_display_name(value, field_name, descriptor, errors):
    path = f"{field_name}.displayName"
    if not isinstance(value, str):
        errors.append(InputFieldError(path, "required" if value is None else "invalid"))
        return None
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        errors.append(InputFieldError(path, "required"))
        return None
    if len(normalized) > descriptor.display_name_limit:
        errors.append(InputFieldError(path, "too_long"))
        return None
    if _contains_control(normalized):
        errors.append(InputFieldError(path, "invalid"))
        return None
    return normalized


def _normalize_uri(value, field_name, descriptor, errors):
    path = f"{field_name}.uri"
    if not isinstance(value, str):
        errors.append(InputFieldError(path, "required" if value is None else "invalid"))
        return None
    normalized = value.strip()
    if not normalized:
        errors.append(InputFieldError(path, "required"))
        return None
    if len(normalized) > descriptor.uri_limit:
        errors.append(InputFieldError(path, "too_long"))
        return None
    if _contains_control(normalized) or any(character.isspace() for character in normalized):
        errors.append(InputFieldError(path, "invalid_uri"))
        return None
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        parsed = None
        hostname = None
    if (
        parsed is None
        or parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        errors.append(InputFieldError(path, "invalid_uri"))
        return None
    return normalized


def _contains_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)
