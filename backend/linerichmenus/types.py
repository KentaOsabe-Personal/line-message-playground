from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID


class OperationKind(StrEnum):
    APPLY = "apply"
    UNLINK = "unlink"
    RELEASE = "release"
    RECHECK = "recheck"
    CLEANUP = "cleanup"


class OperationStatus(StrEnum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CLEANUP_REQUIRED = "cleanup_required"
    RECOVERY_ACTIVE = "recovery_active"
    SUCCEEDED = "succeeded"


class OperationStage(StrEnum):
    CREATING = "creating"
    UPLOADING = "uploading"
    SETTING_DEFAULT = "setting_default"
    VERIFYING = "verifying"
    CLEARING_DEFAULT = "clearing_default"
    CLEANING = "cleaning"
    LOCAL_RELEASE = "local_release"


class ResourceLifecycle(StrEnum):
    CANDIDATE = "candidate"
    APPLIED = "applied"
    OLD = "old"
    CLEANUP_REQUIRED = "cleanup_required"
    DELETED = "deleted"
    RELEASED = "released"


class ObservationKind(StrEnum):
    DEFAULT_NONE = "default_none"
    MANAGED_DEFAULT = "managed_default"
    OTHER_MANAGED_DEFAULT = "other_managed_default"
    EXTERNAL_DEFAULT = "external_default"
    UNKNOWN = "unknown"


class NextAllowedAction(StrEnum):
    NEW_PREVIEW = "new_preview"
    APPLY = "apply"
    UNLINK = "unlink"
    RELEASE = "release"
    RECHECK = "recheck"
    CLEANUP = "cleanup"
    GET_STATE = "get_state"
    VIEW_HISTORY = "view_history"
    CLEAR_TO_DISABLE = "clear_to_disable"


class PreviewWarning(StrEnum):
    EXTERNAL_DEFAULT_REPLACED = "external_default_replaced"
    URL_HISTORY_PERSISTED = "url_history_persisted"
    URL_MUST_NOT_CONTAIN_SECRETS = "url_must_not_contain_secrets"


class DefaultRelation(StrEnum):
    BECAME_DEFAULT = "became_default"
    CLEARED_DEFAULT = "cleared_default"
    NOT_DEFAULT = "not_default"
    EXTERNAL_DEFAULT_PRESERVED = "external_default_preserved"
    UNKNOWN = "unknown"


class CleanupRelation(StrEnum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class SafeResultCode(StrEnum):
    ACCEPTED = "accepted"
    SUCCEEDED = "succeeded"
    NO_CHANGE = "no_change"
    CLEANUP_REQUIRED = "cleanup_required"
    INVALID_INPUT = "invalid_input"
    TEMPLATE_CHANGED = "template_changed"
    IMAGE_INVALID = "image_invalid"
    AUTHENTICATION_REQUIRED = "authentication_required"
    OWNER_OPERATION_BLOCKED = "owner_operation_blocked"
    CHANNEL_UNAVAILABLE = "channel_unavailable"
    CHANNEL_INACTIVE = "channel_inactive"
    STALE_CHANNEL = "stale_channel"
    OPERATION_CONFLICT = "operation_conflict"
    OPERATION_IN_PROGRESS = "operation_in_progress"
    PREVIEW_EXPIRED = "preview_expired"
    INTEGRATION_NOT_READY = "integration_not_ready"
    LINE_REJECTED = "line_rejected"
    TIMEOUT_UNKNOWN = "timeout_unknown"
    RESPONSE_UNKNOWN = "response_unknown"
    OBSERVATION_UNKNOWN = "observation_unknown"
    RATE_LIMITED = "rate_limited"
    STORAGE_RETRYABLE = "storage_retryable"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class TemplateReference:
    template_id: str
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.template_id, str) or not self.template_id:
            raise ValueError("invalid template id")
        if type(self.version) is not int or self.version <= 0:
            raise ValueError("invalid template version")


@dataclass(frozen=True, slots=True)
class TemplateArea:
    field_name: str
    description: str
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if not self.field_name or not self.description:
            raise ValueError("invalid template area metadata")
        if any(type(value) is not int for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("invalid template area geometry")
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("invalid template area geometry")


@dataclass(frozen=True, slots=True)
class TemplateDescriptor:
    reference: TemplateReference
    display_name: str
    width: int
    height: int
    areas: tuple[TemplateArea, ...]
    display_name_limit: int
    uri_limit: int

    def __post_init__(self) -> None:
        if not isinstance(self.reference, TemplateReference) or not self.display_name:
            raise ValueError("invalid template descriptor")
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("invalid template canvas")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("invalid template canvas")
        _require_tuple_of(self.areas, TemplateArea, "template areas")
        if not self.areas:
            raise ValueError("template areas required")
        if self.display_name_limit != 20 or self.uri_limit != 1000:
            raise ValueError("invalid template input limits")

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(area.field_name for area in self.areas)


@dataclass(frozen=True, slots=True, repr=False)
class TemplateInput:
    reference: TemplateReference
    fields: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.reference, TemplateReference):
            raise ValueError("invalid template reference")
        if not isinstance(self.fields, Mapping):
            raise ValueError("invalid template input")
        copied: dict[str, object] = {}
        for key, value in self.fields.items():
            if isinstance(value, Mapping):
                copied[key] = MappingProxyType(dict(value))
            else:
                copied[key] = value
        object.__setattr__(self, "fields", MappingProxyType(copied))

    def __repr__(self) -> str:
        return f"<TemplateInput reference={self.reference!r} fields=redacted>"


@dataclass(frozen=True, slots=True)
class InputFieldError:
    field: str
    reason: str

    def __post_init__(self) -> None:
        if not self.field or self.reason not in {
            "required",
            "unexpected",
            "invalid",
            "too_long",
            "invalid_uri",
            "unknown",
            "unsupported_glyph",
        }:
            raise ValueError("invalid input field error")


@dataclass(frozen=True, slots=True)
class InputRejected:
    errors: tuple[InputFieldError, ...]

    def __post_init__(self) -> None:
        _require_tuple_of(self.errors, InputFieldError, "input errors")
        if not self.errors:
            raise ValueError("input errors required")


@dataclass(frozen=True, slots=True, repr=False)
class RenderedImage:
    content_type: str
    width: int
    height: int
    pixel_digest: str
    binary: bytes

    def __post_init__(self) -> None:
        if self.content_type != "image/png":
            raise ValueError("invalid rendered content type")
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("invalid rendered dimensions")
        _require_sha256(self.pixel_digest, "pixel digest")
        if not isinstance(self.binary, bytes) or not self.binary:
            raise ValueError("rendered binary required")

    def __repr__(self) -> str:
        return (
            f"<RenderedImage content_type={self.content_type!r} "
            f"width={self.width} height={self.height} "
            f"pixel_digest={self.pixel_digest} binary=redacted>"
        )


@dataclass(frozen=True, slots=True)
class RenderRejected:
    code: SafeResultCode
    errors: tuple[InputFieldError, ...] = ()

    def __post_init__(self) -> None:
        if self.code is not SafeResultCode.IMAGE_INVALID:
            raise ValueError("invalid render rejection code")
        _require_tuple_of(self.errors, InputFieldError, "render errors")


@dataclass(frozen=True, slots=True, repr=False)
class TemplateFieldValue:
    display_name: str
    uri: str

    def __post_init__(self) -> None:
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("invalid display name")
        if not isinstance(self.uri, str):
            raise ValueError("invalid uri")
        parsed = urlsplit(self.uri)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("invalid uri")

    def __repr__(self) -> str:
        return "<TemplateFieldValue display_name=redacted uri=redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class NormalizedTemplate:
    reference: TemplateReference
    fields: tuple[TemplateFieldValue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.reference, TemplateReference):
            raise ValueError("invalid template reference")
        _require_tuple_of(self.fields, TemplateFieldValue, "template fields")
        if not self.fields:
            raise ValueError("template fields required")

    def __repr__(self) -> str:
        return (
            f"<NormalizedTemplate reference={self.reference!r} "
            f"field_count={len(self.fields)} values=redacted>"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PreviewSnapshot:
    owner_identity: UUID
    provider_id: str
    channel_public_id: UUID
    channel_revision: datetime
    default_observation_fingerprint: str
    template: NormalizedTemplate
    pixel_digest: str

    def __post_init__(self) -> None:
        _require_uuid(self.owner_identity, "owner identity")
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("invalid provider id")
        _require_uuid(self.channel_public_id, "channel id")
        _require_aware_datetime(self.channel_revision, "channel revision")
        _require_sha256(
            self.default_observation_fingerprint,
            "default observation fingerprint",
        )
        if not isinstance(self.template, NormalizedTemplate):
            raise ValueError("invalid snapshot template")
        _require_sha256(self.pixel_digest, "pixel digest")

    def __repr__(self) -> str:
        return "<PreviewSnapshot axes=redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class IssuedConfirmation:
    token: str
    expires_at: datetime
    usage_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.token, str) or not self.token:
            raise ValueError("confirmation token required")
        _require_aware_datetime(self.expires_at, "confirmation expiry")
        _require_sha256(self.usage_digest, "confirmation usage digest")

    def __repr__(self) -> str:
        return (
            f"<IssuedConfirmation expires_at={self.expires_at.isoformat()} "
            f"usage_digest={self.usage_digest} token=redacted>"
        )


@dataclass(frozen=True, slots=True)
class ConfirmationAccepted:
    usage_digest: str

    def __post_init__(self) -> None:
        _require_sha256(self.usage_digest, "confirmation usage digest")


@dataclass(frozen=True, slots=True)
class ConfirmationRejected:
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in {"preview_expired", "preview_invalid", "preview_changed"}:
            raise ValueError("invalid confirmation rejection")


@dataclass(frozen=True, slots=True)
class PreviewCommand:
    channel_public_id: UUID
    expected_channel_revision: datetime
    template: NormalizedTemplate

    def __post_init__(self) -> None:
        _require_uuid(self.channel_public_id, "channel id")
        _require_aware_datetime(
            self.expected_channel_revision, "expected channel revision"
        )
        if not isinstance(self.template, NormalizedTemplate):
            raise ValueError("invalid preview template")


@dataclass(frozen=True, slots=True, repr=False)
class OperationCommand:
    operation_id: UUID
    channel_public_id: UUID
    expected_channel_revision: datetime
    kind: OperationKind
    subject_operation_id: UUID | None
    target_resource_id: UUID | None
    confirmation_token: str | None = None
    template: TemplateInput | NormalizedTemplate | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id, "operation id")
        _require_uuid(self.channel_public_id, "channel id")
        _require_aware_datetime(
            self.expected_channel_revision, "expected channel revision"
        )
        if not isinstance(self.kind, OperationKind):
            raise ValueError("invalid operation kind")
        _require_optional_uuid(self.subject_operation_id, "subject operation id")
        _require_optional_uuid(self.target_resource_id, "target resource id")
        if self.confirmation_token is not None and (
            not isinstance(self.confirmation_token, str) or not self.confirmation_token
        ):
            raise ValueError("invalid confirmation token")
        if self.template is not None and not isinstance(
            self.template, (TemplateInput, NormalizedTemplate)
        ):
            raise ValueError("invalid operation template")
        _validate_operation_relations(
            kind=self.kind,
            subject_operation_id=self.subject_operation_id,
            target_resource_id=self.target_resource_id,
        )

    def __repr__(self) -> str:
        return (
            f"<OperationCommand operation_id={self.operation_id} "
            f"channel_public_id={self.channel_public_id} kind={self.kind.value} "
            "expected_channel_revision=redacted confirmation_token=redacted "
            "template=redacted>"
        )


@dataclass(frozen=True, slots=True)
class OperationView:
    operation_id: UUID
    kind: OperationKind
    status: OperationStatus
    stage: OperationStage | None
    result: SafeResultCode
    subject_operation_id: UUID | None
    target_resource_id: UUID | None
    accepted_at: datetime
    completed_at: datetime | None
    next_allowed_actions: tuple[NextAllowedAction, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id, "operation id")
        if not isinstance(self.kind, OperationKind):
            raise ValueError("invalid operation kind")
        if not isinstance(self.status, OperationStatus):
            raise ValueError("invalid operation status")
        if self.stage is not None and not isinstance(self.stage, OperationStage):
            raise ValueError("invalid operation stage")
        if not isinstance(self.result, SafeResultCode):
            raise ValueError("invalid operation result")
        _require_optional_uuid(self.subject_operation_id, "subject operation id")
        _require_optional_uuid(self.target_resource_id, "target resource id")
        _require_aware_datetime(self.accepted_at, "accepted at")
        if self.completed_at is not None:
            _require_aware_datetime(self.completed_at, "completed at")
        _require_tuple_of(
            self.next_allowed_actions, NextAllowedAction, "next allowed actions"
        )
        _validate_operation_relations(
            kind=self.kind,
            subject_operation_id=self.subject_operation_id,
            target_resource_id=self.target_resource_id,
        )


@dataclass(frozen=True, slots=True)
class ManagedResourceView:
    public_id: UUID
    origin_operation_id: UUID
    lifecycle: ResourceLifecycle
    image_digest: str

    def __post_init__(self) -> None:
        _require_uuid(self.public_id, "resource id")
        _require_uuid(self.origin_operation_id, "origin operation id")
        if not isinstance(self.lifecycle, ResourceLifecycle):
            raise ValueError("invalid resource lifecycle")
        _require_sha256(self.image_digest, "image digest")


@dataclass(frozen=True, slots=True)
class DefaultObservation:
    kind: ObservationKind
    observed_at: datetime
    fingerprint: str
    managed_resource_id: UUID | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ObservationKind):
            raise ValueError("invalid observation kind")
        _require_aware_datetime(self.observed_at, "observed at")
        _require_sha256(self.fingerprint, "observation fingerprint")
        _require_optional_uuid(self.managed_resource_id, "managed resource id")
        requires_resource = self.kind in {
            ObservationKind.MANAGED_DEFAULT,
            ObservationKind.OTHER_MANAGED_DEFAULT,
        }
        if requires_resource != (self.managed_resource_id is not None):
            raise ValueError("invalid observation relation")


@dataclass(frozen=True, slots=True, repr=False)
class PreviewView:
    channel_public_id: UUID
    channel_label: str
    template: NormalizedTemplate
    image_digest: str
    observation: DefaultObservation
    expires_at: datetime
    warnings: tuple[PreviewWarning, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.channel_public_id, "channel id")
        if not isinstance(self.channel_label, str) or not self.channel_label:
            raise ValueError("invalid channel label")
        if not isinstance(self.template, NormalizedTemplate):
            raise ValueError("invalid preview template")
        _require_sha256(self.image_digest, "image digest")
        if not isinstance(self.observation, DefaultObservation):
            raise ValueError("invalid preview observation")
        _require_aware_datetime(self.expires_at, "preview expiry")
        _require_tuple_of(self.warnings, PreviewWarning, "preview warnings")

    def __repr__(self) -> str:
        return (
            f"<PreviewView channel_public_id={self.channel_public_id} "
            f"template={self.template.reference!r} image_digest={self.image_digest} "
            "channel_label=redacted fields=redacted>"
        )


@dataclass(frozen=True, slots=True)
class HistorySummary:
    total_count: int
    latest_operation_id: UUID | None
    latest_status: OperationStatus | None

    def __post_init__(self) -> None:
        if type(self.total_count) is not int or self.total_count < 0:
            raise ValueError("invalid history count")
        _require_optional_uuid(self.latest_operation_id, "latest operation id")
        if self.latest_status is not None and not isinstance(
            self.latest_status, OperationStatus
        ):
            raise ValueError("invalid latest operation status")
        latest_present = (
            self.latest_operation_id is not None and self.latest_status is not None
        )
        if latest_present != (self.total_count > 0):
            raise ValueError("inconsistent history summary")


@dataclass(frozen=True, slots=True)
class ChannelStateView:
    channel_public_id: UUID
    current_resource: ManagedResourceView | None
    blocking_operation: OperationView | None
    active_operation: OperationView | None
    cleanup_resources: tuple[ManagedResourceView, ...]
    latest_observation: DefaultObservation | None
    history_summary: HistorySummary
    next_allowed_actions: tuple[NextAllowedAction, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.channel_public_id, "channel id")
        if self.current_resource is not None and not isinstance(
            self.current_resource, ManagedResourceView
        ):
            raise ValueError("invalid current resource")
        for name, operation in (
            ("blocking operation", self.blocking_operation),
            ("active operation", self.active_operation),
        ):
            if operation is not None and not isinstance(operation, OperationView):
                raise ValueError(f"invalid {name}")
        _require_tuple_of(
            self.cleanup_resources, ManagedResourceView, "cleanup resources"
        )
        if self.latest_observation is not None and not isinstance(
            self.latest_observation, DefaultObservation
        ):
            raise ValueError("invalid latest observation")
        if not isinstance(self.history_summary, HistorySummary):
            raise ValueError("invalid history summary")
        _require_tuple_of(
            self.next_allowed_actions, NextAllowedAction, "next allowed actions"
        )


@dataclass(frozen=True, slots=True, repr=False)
class HistoryEntry:
    operation: OperationView
    channel_public_id: UUID
    channel_label: str
    configuration: NormalizedTemplate | None
    transitions: tuple[SafeResultCode, ...]
    default_relation: DefaultRelation
    cleanup_relation: CleanupRelation

    def __post_init__(self) -> None:
        if not isinstance(self.operation, OperationView):
            raise ValueError("invalid history operation")
        _require_uuid(self.channel_public_id, "history channel id")
        if not isinstance(self.channel_label, str) or not self.channel_label:
            raise ValueError("invalid channel label")
        if self.configuration is not None and not isinstance(
            self.configuration, NormalizedTemplate
        ):
            raise ValueError("invalid history configuration")
        _require_tuple_of(self.transitions, SafeResultCode, "history transitions")
        if not isinstance(self.default_relation, DefaultRelation):
            raise ValueError("invalid default relation")
        if not isinstance(self.cleanup_relation, CleanupRelation):
            raise ValueError("invalid cleanup relation")

    def __repr__(self) -> str:
        return (
            f"<HistoryEntry operation={self.operation!r} "
            "channel_label=redacted configuration=redacted "
            f"transition_count={len(self.transitions)}>"
        )


@dataclass(frozen=True, slots=True)
class HistoryPage:
    entries: tuple[HistoryEntry, ...]
    next_cursor: str | None
    has_more: bool

    def __post_init__(self) -> None:
        _require_tuple_of(self.entries, HistoryEntry, "history entries")
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str) or not self.next_cursor
        ):
            raise ValueError("invalid history cursor")
        if type(self.has_more) is not bool:
            raise ValueError("invalid history continuation")
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("inconsistent history continuation")


@dataclass(frozen=True, slots=True)
class SafeError:
    code: SafeResultCode
    next_allowed_actions: tuple[NextAllowedAction, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.code, SafeResultCode):
            raise ValueError("invalid safe error code")
        _require_tuple_of(
            self.next_allowed_actions, NextAllowedAction, "next allowed actions"
        )

    @classmethod
    def from_untrusted(
        cls,
        *,
        code: SafeResultCode,
        next_allowed_actions: tuple[NextAllowedAction, ...],
        error: BaseException,
    ) -> SafeError:
        del error
        return cls(code=code, next_allowed_actions=next_allowed_actions)


@dataclass(frozen=True, slots=True)
class MutationReady:
    pass


@dataclass(frozen=True, slots=True)
class IntegrationNotReady:
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in {"integration_not_ready", "unsupported_operation"}:
            raise ValueError("invalid readiness rejection")


def _require_tuple_of(value: object, expected: type, name: str) -> None:
    if not isinstance(value, tuple) or not all(
        isinstance(item, expected) for item in value
    ):
        raise ValueError(f"invalid {name}")


def _require_uuid(value: object, name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"invalid {name}")


def _require_optional_uuid(value: object, name: str) -> None:
    if value is not None:
        _require_uuid(value, name)


def _require_aware_datetime(value: object, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"invalid {name}")


def _require_sha256(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"invalid {name}")


def _validate_operation_relations(
    *,
    kind: OperationKind,
    subject_operation_id: UUID | None,
    target_resource_id: UUID | None,
) -> None:
    subject_present = subject_operation_id is not None
    target_present = target_resource_id is not None
    expected = {
        OperationKind.APPLY: (False, False),
        OperationKind.UNLINK: (False, True),
        OperationKind.RELEASE: (False, True),
        OperationKind.RECHECK: (True, False),
        OperationKind.CLEANUP: (True, True),
    }[kind]
    if (subject_present, target_present) != expected:
        raise ValueError("invalid operation relation")
