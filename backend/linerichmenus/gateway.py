from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

from django.utils import timezone

from linechannels.types import AccessToken

from .types import RenderedImage


class _SerializationDisabled:
    __slots__ = ()

    def __reduce__(self) -> object:
        raise TypeError("serialization is disabled")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("serialization is disabled")


@dataclass(frozen=True, slots=True, repr=False)
class RichMenuGatewayContext(_SerializationDisabled):
    channel_public_id: UUID
    channel_revision: datetime
    access_token: AccessToken

    def __post_init__(self) -> None:
        if not isinstance(self.channel_public_id, UUID):
            raise ValueError("invalid channel public id")
        if (
            not isinstance(self.channel_revision, datetime)
            or timezone.is_naive(self.channel_revision)
        ):
            raise ValueError("invalid channel revision")
        if not isinstance(self.access_token, AccessToken):
            raise ValueError("invalid access token")

    def __repr__(self) -> str:
        return (
            "<RichMenuGatewayContext "
            f"channel_public_id={self.channel_public_id} "
            f"channel_revision={self.channel_revision.isoformat()} "
            "access_token=redacted>"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class RichMenuBounds:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("invalid rich menu bounds")
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("invalid rich menu bounds")


@dataclass(frozen=True, slots=True, repr=False)
class RichMenuUriAction(_SerializationDisabled):
    uri: str

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str) or not self.uri:
            raise ValueError("invalid rich menu uri action")

    def __repr__(self) -> str:
        return "<RichMenuUriAction uri=redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class RichMenuArea(_SerializationDisabled):
    bounds: RichMenuBounds
    action: RichMenuUriAction

    def __post_init__(self) -> None:
        if not isinstance(self.bounds, RichMenuBounds):
            raise ValueError("invalid rich menu area bounds")
        if not isinstance(self.action, RichMenuUriAction):
            raise ValueError("invalid rich menu area action")

    def __repr__(self) -> str:
        return "<RichMenuArea bounds=redacted action=redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class RichMenuObject(_SerializationDisabled):
    width: int
    height: int
    name: str
    chat_bar_text: str
    areas: tuple[RichMenuArea, ...]
    selected: bool = False

    def __post_init__(self) -> None:
        if type(self.width) is not int or not 800 <= self.width <= 2500:
            raise ValueError("invalid rich menu width")
        if type(self.height) is not int or self.height < 250:
            raise ValueError("invalid rich menu height")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("invalid rich menu name")
        if not isinstance(self.chat_bar_text, str) or not self.chat_bar_text:
            raise ValueError("invalid rich menu chat bar text")
        if type(self.selected) is not bool:
            raise ValueError("invalid rich menu selection")
        if not isinstance(self.areas, tuple) or not self.areas or not all(
            isinstance(area, RichMenuArea) for area in self.areas
        ):
            raise ValueError("invalid rich menu areas")

    def to_payload(self) -> dict[str, object]:
        return {
            "size": {"width": self.width, "height": self.height},
            "selected": self.selected,
            "name": self.name,
            "chatBarText": self.chat_bar_text,
            "areas": [
                {
                    "bounds": {
                        "x": area.bounds.x,
                        "y": area.bounds.y,
                        "width": area.bounds.width,
                        "height": area.bounds.height,
                    },
                    "action": {"type": "uri", "uri": area.action.uri},
                }
                for area in self.areas
            ],
        }

    def __repr__(self) -> str:
        return (
            f"<RichMenuObject width={self.width} height={self.height} "
            f"area_count={len(self.areas)} name=redacted actions=redacted>"
        )

    __str__ = __repr__


GatewayRejectedCode = Literal["line_rejected", "invalid_input", "image_invalid"]
GatewayUnknownCode = Literal["timeout_unknown", "response_unknown", "rate_limited"]


@dataclass(frozen=True, slots=True)
class GatewayAccepted:
    status: Literal["accepted"] = "accepted"


@dataclass(frozen=True, slots=True)
class GatewayRejected:
    code: GatewayRejectedCode
    status: Literal["rejected"] = "rejected"

    def __post_init__(self) -> None:
        if self.code not in {"line_rejected", "invalid_input", "image_invalid"}:
            raise ValueError("invalid gateway rejection")


@dataclass(frozen=True, slots=True)
class GatewayUnknown:
    code: GatewayUnknownCode = "response_unknown"
    status: Literal["unknown"] = "unknown"

    def __post_init__(self) -> None:
        if self.code not in {"timeout_unknown", "response_unknown", "rate_limited"}:
            raise ValueError("invalid gateway unknown result")


@dataclass(frozen=True, slots=True, repr=False)
class CreateAccepted(_SerializationDisabled):
    line_rich_menu_id: str
    status: Literal["accepted"] = "accepted"

    def __post_init__(self) -> None:
        _require_line_id(self.line_rich_menu_id)

    def __repr__(self) -> str:
        return "<CreateAccepted line_rich_menu_id=redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class ResourceSummary(_SerializationDisabled):
    line_rich_menu_id: str
    name: str

    def __post_init__(self) -> None:
        _require_line_id(self.line_rich_menu_id)
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("invalid rich menu resource name")

    def __repr__(self) -> str:
        return "<ResourceSummary line_rich_menu_id=redacted name=redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class ResourceListAccepted(_SerializationDisabled):
    resources: tuple[ResourceSummary, ...]
    status: Literal["accepted"] = "accepted"

    def __post_init__(self) -> None:
        if not isinstance(self.resources, tuple) or not all(
            isinstance(resource, ResourceSummary) for resource in self.resources
        ):
            raise ValueError("invalid resource list")

    def __repr__(self) -> str:
        return f"<ResourceListAccepted resource_count={len(self.resources)}>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class ResourceObserved(_SerializationDisabled):
    resource: ResourceSummary
    status: Literal["observed"] = "observed"

    def __post_init__(self) -> None:
        if not isinstance(self.resource, ResourceSummary):
            raise ValueError("invalid resource observation")

    @property
    def line_rich_menu_id(self) -> str:
        return self.resource.line_rich_menu_id

    @property
    def name(self) -> str:
        return self.resource.name

    def __repr__(self) -> str:
        return "<ResourceObserved resource=redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class ResourceAbsent:
    status: Literal["absent"] = "absent"


@dataclass(frozen=True, slots=True)
class ResourceObservationUnknown:
    code: GatewayUnknownCode = "response_unknown"
    status: Literal["unknown"] = "unknown"

    def __post_init__(self) -> None:
        if self.code not in {"timeout_unknown", "response_unknown", "rate_limited"}:
            raise ValueError("invalid resource observation unknown")


@dataclass(frozen=True, slots=True, repr=False)
class RichMenuDefaultPresent(_SerializationDisabled):
    line_rich_menu_id: str
    status: Literal["present"] = "present"

    def __post_init__(self) -> None:
        _require_line_id(self.line_rich_menu_id)

    def __repr__(self) -> str:
        return "<RichMenuDefaultPresent line_rich_menu_id=redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class RichMenuDefaultNone:
    status: Literal["none"] = "none"


@dataclass(frozen=True, slots=True)
class RichMenuDefaultExternal:
    status: Literal["external"] = "external"


@dataclass(frozen=True, slots=True)
class RichMenuDefaultUnknown:
    code: GatewayUnknownCode = "response_unknown"
    status: Literal["unknown"] = "unknown"

    def __post_init__(self) -> None:
        if self.code not in {"timeout_unknown", "response_unknown", "rate_limited"}:
            raise ValueError("invalid default observation unknown")


@dataclass(frozen=True, slots=True, repr=False)
class ImageObserved(_SerializationDisabled):
    content_type: str
    width: int
    height: int
    pixel_digest: str
    byte_size: int
    status: Literal["observed"] = "observed"

    def __post_init__(self) -> None:
        if self.content_type not in {"image/png", "image/jpeg"}:
            raise ValueError("invalid image content type")
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("invalid image dimensions")
        if not (
            800 <= self.width <= 2500
            and self.height >= 250
            and self.width / self.height >= 1.45
        ):
            raise ValueError("invalid image dimensions")
        if type(self.byte_size) is not int or self.byte_size <= 0:
            raise ValueError("invalid image size")
        if self.byte_size > 1024 * 1024:
            raise ValueError("invalid image size")
        if not _is_sha256(self.pixel_digest):
            raise ValueError("invalid image digest")

    def __repr__(self) -> str:
        return (
            f"<ImageObserved content_type={self.content_type!r} "
            f"width={self.width} height={self.height} "
            f"pixel_digest={self.pixel_digest} byte_size={self.byte_size}>"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class ImageObservationUnknown:
    code: GatewayUnknownCode = "response_unknown"
    status: Literal["unknown"] = "unknown"

    def __post_init__(self) -> None:
        if self.code not in {"timeout_unknown", "response_unknown", "rate_limited"}:
            raise ValueError("invalid image observation unknown")


MutationResult = GatewayAccepted | GatewayRejected | GatewayUnknown
CreateResult = CreateAccepted | GatewayRejected | GatewayUnknown
ResourceListObservation = ResourceListAccepted | GatewayRejected | GatewayUnknown
ResourceObservation = (
    ResourceObserved | ResourceAbsent | ResourceObservationUnknown | GatewayRejected
)
DefaultObservation = (
    RichMenuDefaultPresent
    | RichMenuDefaultNone
    | RichMenuDefaultExternal
    | RichMenuDefaultUnknown
    | GatewayRejected
)
ImageObservation = ImageObserved | ImageObservationUnknown | GatewayRejected


class RichMenuGateway(Protocol):
    def validate(self, context: RichMenuGatewayContext, request: RichMenuObject) -> MutationResult: ...

    def create(self, context: RichMenuGatewayContext, request: RichMenuObject) -> CreateResult: ...

    def upload(
        self, context: RichMenuGatewayContext, rich_menu_id: str, image: RenderedImage
    ) -> MutationResult: ...

    def download(
        self, context: RichMenuGatewayContext, rich_menu_id: str
    ) -> ImageObservation: ...

    def list_resources(self, context: RichMenuGatewayContext) -> ResourceListObservation: ...

    def get_resource(self, context: RichMenuGatewayContext, rich_menu_id: str) -> ResourceObservation: ...

    def set_default(self, context: RichMenuGatewayContext, rich_menu_id: str) -> MutationResult: ...

    def get_default(self, context: RichMenuGatewayContext) -> DefaultObservation: ...

    def clear_default(self, context: RichMenuGatewayContext) -> MutationResult: ...

    def delete(self, context: RichMenuGatewayContext, rich_menu_id: str) -> MutationResult: ...


class _SdkRichMenuClients(Protocol):
    json: object
    blob: object

    def close(self) -> None: ...


class _SdkRichMenuClientFactory(Protocol):
    def __call__(self, access_token: str, *, retries: int) -> _SdkRichMenuClients: ...


def _build_sdk_clients(access_token: str, *, retries: int) -> _SdkRichMenuClients:
    from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, MessagingApiBlob

    configuration = Configuration(access_token=access_token)
    configuration.retries = retries
    api_client = ApiClient(configuration)
    return _SdkClients(
        json_api=MessagingApi(api_client),
        blob_api=MessagingApiBlob(api_client),
        api_client=api_client,
    )


@dataclass(frozen=True, slots=True)
class _SdkClients:
    json_api: object
    blob_api: object
    api_client: object

    @property
    def json(self) -> object:
        return self.json_api

    @property
    def blob(self) -> object:
        return self.blob_api

    def close(self) -> None:
        close = getattr(self.api_client, "close", None)
        if callable(close):
            close()


class DefaultRichMenuGateway:
    """LINE rich-menu SDK adapter with one scoped call and no SDK leakage."""

    def __init__(
        self,
        client_factory: _SdkRichMenuClientFactory = _build_sdk_clients,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("invalid timeout")
        self._client_factory = client_factory
        self._timeout_seconds = float(timeout_seconds)

    def __repr__(self) -> str:
        return f"<DefaultRichMenuGateway timeout_seconds={self._timeout_seconds}>"

    def validate(self, context: RichMenuGatewayContext, request: RichMenuObject) -> MutationResult:
        if not isinstance(request, RichMenuObject):
            return GatewayRejected("invalid_input")
        sdk_request = _safe_sdk_request(request)
        if sdk_request is None:
            return GatewayRejected("invalid_input")
        return self._run_json(context, "validate_rich_menu_object", sdk_request, mutation=True)

    def create(self, context: RichMenuGatewayContext, request: RichMenuObject) -> CreateResult:
        if not isinstance(request, RichMenuObject):
            return GatewayRejected("invalid_input")
        sdk_request = _safe_sdk_request(request)
        if sdk_request is None:
            return GatewayRejected("invalid_input")

        def handle(response):
            line_id = _object_value(response, "richMenuId", "rich_menu_id")
            if not isinstance(line_id, str) or not line_id:
                return GatewayUnknown("response_unknown")
            try:
                return CreateAccepted(line_id)
            except ValueError:
                return GatewayUnknown("response_unknown")

        return self._run_json(
            context,
            "create_rich_menu",
            sdk_request,
            handler=handle,
        )

    def upload(self, context: RichMenuGatewayContext, rich_menu_id: str, image: RenderedImage) -> MutationResult:
        if not _valid_line_id(rich_menu_id):
            return GatewayRejected("invalid_input")
        if not isinstance(image, RenderedImage) or not _valid_upload_image(image):
            return GatewayRejected("image_invalid")
        return self._run_blob(context, "set_rich_menu_image", rich_menu_id, image.binary, mutation=True)

    def download(self, context: RichMenuGatewayContext, rich_menu_id: str) -> ImageObservation:
        if not _valid_line_id(rich_menu_id):
            return ImageObservationUnknown("response_unknown")

        def handle(response):
            binary = _read_blob(response)
            if binary is None:
                return ImageObservationUnknown("response_unknown")
            return _decode_image(binary)

        return self._run_blob(context, "get_rich_menu_image", rich_menu_id, handler=handle)

    def list_resources(self, context: RichMenuGatewayContext) -> ResourceListObservation:
        def handle(response):
            raw_resources = _object_value(response, "richmenus", "rich_menus")
            if not isinstance(raw_resources, (list, tuple)):
                return GatewayUnknown("response_unknown")
            resources: list[ResourceSummary] = []
            for raw in raw_resources:
                line_id = _object_value(raw, "richMenuId", "rich_menu_id")
                name = _object_value(raw, "name")
                if not isinstance(line_id, str) or not isinstance(name, str):
                    return GatewayUnknown("response_unknown")
                try:
                    resources.append(ResourceSummary(line_id, name))
                except ValueError:
                    return GatewayUnknown("response_unknown")
            return ResourceListAccepted(tuple(resources))

        return self._run_json(context, "get_rich_menu_list", handler=handle)

    def get_resource(self, context: RichMenuGatewayContext, rich_menu_id: str) -> ResourceObservation:
        if not _valid_line_id(rich_menu_id):
            return ResourceObservationUnknown("response_unknown")

        def handle(response):
            line_id = _object_value(response, "richMenuId", "rich_menu_id")
            name = _object_value(response, "name")
            if not isinstance(line_id, str) or not isinstance(name, str):
                return ResourceObservationUnknown("response_unknown")
            try:
                return ResourceObserved(ResourceSummary(line_id, name))
            except ValueError:
                return ResourceObservationUnknown("response_unknown")

        return self._run_json(
            context,
            "get_rich_menu",
            rich_menu_id,
            handler=handle,
            not_found=ResourceAbsent(),
        )

    def set_default(self, context: RichMenuGatewayContext, rich_menu_id: str) -> MutationResult:
        if not _valid_line_id(rich_menu_id):
            return GatewayRejected("invalid_input")
        return self._run_json(context, "set_default_rich_menu", rich_menu_id, mutation=True)

    def get_default(self, context: RichMenuGatewayContext) -> DefaultObservation:
        def handle(response):
            line_id = _object_value(response, "richMenuId", "rich_menu_id")
            if not isinstance(line_id, str) or not _valid_line_id(line_id):
                return RichMenuDefaultUnknown("response_unknown")
            return RichMenuDefaultPresent(line_id)

        return self._run_json(
            context,
            "get_default_rich_menu",
            handler=handle,
            not_found=RichMenuDefaultNone(),
            forbidden=RichMenuDefaultExternal(),
        )

    def clear_default(self, context: RichMenuGatewayContext) -> MutationResult:
        return self._run_json(context, "cancel_default_rich_menu", mutation=True)

    def delete(self, context: RichMenuGatewayContext, rich_menu_id: str) -> MutationResult:
        if not _valid_line_id(rich_menu_id):
            return GatewayRejected("invalid_input")
        return self._run_json(context, "delete_rich_menu", rich_menu_id, mutation=True)

    def _run_json(
        self,
        context: RichMenuGatewayContext,
        method_name: str,
        *args: object,
        handler: Callable[[object], object] | None = None,
        mutation: bool = False,
        not_found: object | None = None,
        forbidden: object | None = None,
    ):
        return self._run(
            context,
            "json",
            method_name,
            *args,
            handler=handler,
            mutation=mutation,
            not_found=not_found,
            forbidden=forbidden,
        )

    def _run_blob(
        self,
        context: RichMenuGatewayContext,
        method_name: str,
        *args: object,
        handler: Callable[[object], object] | None = None,
        mutation: bool = False,
    ):
        return self._run(
            context,
            "blob",
            method_name,
            *args,
            handler=handler,
            mutation=mutation,
        )

    def _run(
        self,
        context: RichMenuGatewayContext,
        client_kind: Literal["json", "blob"],
        method_name: str,
        *args: object,
        handler: Callable[[object], object] | None,
        mutation: bool,
        not_found: object | None = None,
        forbidden: object | None = None,
    ):
        if not isinstance(context, RichMenuGatewayContext):
            raise TypeError("rich menu gateway context required")
        clients = None
        result: object
        try:
            clients = self._client_factory(
                context.access_token.reveal_for_use(),
                retries=0,
            )
            client = getattr(clients, client_kind)
            method = getattr(client, method_name)
            response = method(*args, _request_timeout=self._timeout_seconds)
            if handler is not None:
                result = handler(response)
            elif mutation:
                result = GatewayAccepted()
            else:
                result = GatewayUnknown("response_unknown")
        except Exception as error:
            result = _classify_exception(
                error,
                not_found=not_found,
                forbidden=forbidden,
            )
        finally:
            if clients is not None:
                try:
                    clients.close()
                except Exception:
                    result = GatewayUnknown("response_unknown")
        return result


def _classify_exception(
    error: Exception,
    *,
    not_found: object | None,
    forbidden: object | None,
):
    status = getattr(error, "status", None)
    if status == 404 and not_found is not None:
        return not_found
    if status == 403 and forbidden is not None:
        return forbidden
    if isinstance(status, int) and 400 <= status < 500 and status != 429:
        return GatewayRejected("line_rejected")
    if status == 429:
        return GatewayUnknown("rate_limited")
    if isinstance(status, int) and status >= 500:
        return GatewayUnknown("response_unknown")
    if isinstance(error, TimeoutError) or error.__class__.__name__.lower().endswith("timeout"):
        return GatewayUnknown("timeout_unknown")
    return GatewayUnknown("response_unknown")


def _sdk_request(request: RichMenuObject):
    try:
        from linebot.v3.messaging import (
            RichMenuArea as SdkRichMenuArea,
            RichMenuBounds as SdkRichMenuBounds,
            RichMenuRequest as SdkRichMenuRequest,
            RichMenuSize as SdkRichMenuSize,
            URIAction,
        )
    except (ImportError, AttributeError):
        return request.to_payload()
    return SdkRichMenuRequest(
        size=SdkRichMenuSize(width=request.width, height=request.height),
        selected=request.selected,
        name=request.name,
        chatBarText=request.chat_bar_text,
        areas=[
            SdkRichMenuArea(
                bounds=SdkRichMenuBounds(
                    x=area.bounds.x,
                    y=area.bounds.y,
                    width=area.bounds.width,
                    height=area.bounds.height,
                ),
                action=URIAction(uri=area.action.uri),
            )
            for area in request.areas
        ],
    )


def _safe_sdk_request(request: RichMenuObject):
    try:
        return _sdk_request(request)
    except Exception:
        return None


def _object_value(value: object, *names: str):
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


class _BlobCloseFailure(Exception):
    pass


def _read_blob(value: object) -> bytes | None:
    close = None
    try:
        close = getattr(value, "close", None)
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        data = getattr(value, "data", None)
        if not isinstance(data, (bytes, bytearray)):
            read = getattr(value, "read", None)
            if not callable(read):
                return None
            data = read()
        if not isinstance(data, (bytes, bytearray)):
            return None
        return bytes(data)
    except Exception:
        return None
    finally:
        if callable(close):
            try:
                close()
            except Exception:
                # The caller sees an observation-unknown result when the SDK
                # response cannot be closed; do not expose the raw exception.
                raise _BlobCloseFailure from None


def _decode_image(binary: bytes) -> ImageObservation:
    if not binary or len(binary) > 1024 * 1024:
        return ImageObservationUnknown("response_unknown")
    try:
        from io import BytesIO
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return ImageObservationUnknown("response_unknown")
    try:
        with Image.open(BytesIO(binary)) as image:
            image.load()
            if image.format == "PNG":
                content_type = "image/png"
            elif image.format in {"JPEG", "JPG"}:
                content_type = "image/jpeg"
            else:
                return ImageObservationUnknown("response_unknown")
            width, height = image.size
            if not (
                800 <= width <= 2500
                and height >= 250
                and width / height >= 1.45
            ):
                return ImageObservationUnknown("response_unknown")
            rgba = image.convert("RGBA").tobytes()
    except Exception:
        return ImageObservationUnknown("response_unknown")
    return ImageObserved(
        content_type=content_type,
        width=width,
        height=height,
        pixel_digest=_pixel_digest(width, height, rgba),
        byte_size=len(binary),
    )


def _valid_upload_image(image: RenderedImage) -> bool:
    if image.content_type not in {"image/png", "image/jpeg"}:
        return False
    if len(image.binary) > 1024 * 1024:
        return False
    decoded = _decode_image(image.binary)
    return (
        isinstance(decoded, ImageObserved)
        and decoded.content_type == image.content_type
        and decoded.width == image.width
        and decoded.height == image.height
    )


def _pixel_digest(width: int, height: int, rgba: bytes) -> str:
    digest = sha256()
    for component in (str(width).encode("ascii"), str(height).encode("ascii"), rgba):
        digest.update(len(component).to_bytes(8, "big"))
        digest.update(component)
    return digest.hexdigest()


def _valid_line_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value.isascii()
        and value.isprintable()
        and not any(character.isspace() for character in value)
    )


def _require_line_id(value: object) -> None:
    if not _valid_line_id(value):
        raise ValueError("invalid line rich menu id")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
