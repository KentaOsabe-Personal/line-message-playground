"""現在閲覧のrequest内比較値と公開契約。画像・設定は永続化しない。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from linechannels.admin_lifecycle_types import DeactivationConflict, DeactivationView

from .types import (
    ChannelStateView,
    NormalizedTemplate,
    OperationKind,
    OperationStage,
    OperationStatus,
    OwnerChannelScope,
    ResourceLifecycle,
    SafeResultCode,
    _require_aware_datetime,
    _require_optional_uuid,
    _require_sha256,
    _require_tuple_of,
    _require_uuid,
)


@dataclass(frozen=True, slots=True, repr=False)
class CurrentImageAvailable:
    content_type: Literal["image/png", "image/jpeg"]
    width: int
    height: int
    pixel_digest: str
    binary: bytes
    status: Literal["available"] = "available"

    def __post_init__(self) -> None:
        if self.status != "available" or self.content_type not in {"image/png", "image/jpeg"}:
            raise ValueError("invalid current image format")
        if (
            type(self.width) is not int
            or type(self.height) is not int
            or not 800 <= self.width <= 2500
            or self.height < 250
            or self.width / self.height < 1.45
        ):
            raise ValueError("invalid current image dimensions")
        _require_sha256(self.pixel_digest, "current image digest")
        if not isinstance(self.binary, bytes) or not 0 < len(self.binary) <= 1024 * 1024:
            raise ValueError("invalid current image bytes")

    def __repr__(self) -> str:
        return "<CurrentImageAvailable binary=redacted>"


@dataclass(frozen=True, slots=True)
class CurrentImageUnavailable:
    reason: Literal["missing", "invalid", "fetch_failed", "rate_limited"]
    status: Literal["unavailable"] = "unavailable"

    def __post_init__(self) -> None:
        if self.status != "unavailable" or self.reason not in {
            "missing",
            "invalid",
            "fetch_failed",
            "rate_limited",
        }:
            raise ValueError("invalid current image failure")


@dataclass(frozen=True, slots=True, repr=False)
class ConfigurationAvailable:
    template: NormalizedTemplate
    editability: Literal["editable", "template_unavailable"]
    status: Literal["available"] = "available"

    def __post_init__(self) -> None:
        if self.status != "available" or not isinstance(self.template, NormalizedTemplate):
            raise ValueError("invalid current configuration")
        if self.editability not in {"editable", "template_unavailable"}:
            raise ValueError("invalid configuration editability")

    def __repr__(self) -> str:
        return "<ConfigurationAvailable fields=redacted>"


@dataclass(frozen=True, slots=True)
class ConfigurationUnavailable:
    reason: Literal["mapping_missing", "configuration_invalid"]
    status: Literal["unavailable"] = "unavailable"

    def __post_init__(self) -> None:
        if self.status != "unavailable" or self.reason not in {
            "mapping_missing",
            "configuration_invalid",
        }:
            raise ValueError("invalid configuration failure")


CurrentImage = CurrentImageAvailable | CurrentImageUnavailable
CurrentConfiguration = ConfigurationAvailable | ConfigurationUnavailable


@dataclass(frozen=True, slots=True, repr=False)
class CurrentContent:
    kind: Literal["managed", "unset", "external", "attention", "inactive"]
    resource_id: UUID | None = None
    image: CurrentImage | None = None
    configuration: CurrentConfiguration | None = None

    def __post_init__(self) -> None:
        if self.kind == "managed":
            _require_uuid(self.resource_id, "current resource")
            if not isinstance(self.image, (CurrentImageAvailable, CurrentImageUnavailable)):
                raise ValueError("invalid current image")
            if not isinstance(
                self.configuration, (ConfigurationAvailable, ConfigurationUnavailable)
            ):
                raise ValueError("invalid current configuration")
        elif self.kind not in {"unset", "external", "attention", "inactive"} or any(
            value is not None for value in (self.resource_id, self.image, self.configuration)
        ):
            raise ValueError("invalid current content")

    def __repr__(self) -> str:
        return f"<CurrentContent kind={self.kind!r} content=redacted>"


@dataclass(frozen=True, slots=True)
class RecoveryTarget:
    kind: Literal["cleanup"]
    subject_operation_id: UUID
    target_resource_id: UUID

    def __post_init__(self) -> None:
        if self.kind != "cleanup":
            raise ValueError("invalid recovery target")
        _require_uuid(self.subject_operation_id, "recovery subject")
        _require_uuid(self.target_resource_id, "recovery resource")


@dataclass(frozen=True, slots=True)
class CurrentStateSnapshot:
    """Noneのstateと全参照が空の既存行を区別する。観測更新日時は比較しない。"""

    current_resource_id: UUID | None
    active_operation_id: UUID | None
    blocking_operation_id: UUID | None

    def __post_init__(self) -> None:
        for value in (
            self.current_resource_id,
            self.active_operation_id,
            self.blocking_operation_id,
        ):
            _require_optional_uuid(value, "current state reference")


@dataclass(frozen=True, slots=True, repr=False)
class CurrentResourceSnapshot:
    public_id: UUID
    channel_public_id: UUID
    origin_operation_id: UUID
    replacement_operation_id: UUID | None
    line_rich_menu_id: str | None
    lifecycle: ResourceLifecycle
    image_digest: str
    updated_at: datetime

    def __post_init__(self) -> None:
        for value in (self.public_id, self.channel_public_id, self.origin_operation_id):
            _require_uuid(value, "current resource reference")
        _require_optional_uuid(self.replacement_operation_id, "replacement operation")
        if self.line_rich_menu_id is not None and (
            not isinstance(self.line_rich_menu_id, str) or not self.line_rich_menu_id
        ):
            raise ValueError("invalid current LINE reference")
        if not isinstance(self.lifecycle, ResourceLifecycle):
            raise ValueError("invalid current resource lifecycle")
        _require_sha256(self.image_digest, "current resource digest")
        _require_aware_datetime(self.updated_at, "current resource updated at")

    def __repr__(self) -> str:
        return "<CurrentResourceSnapshot references=redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class CurrentOperationSnapshot:
    operation_id: UUID
    channel_public_id: UUID
    owner_identity_public_id: UUID
    provider_id: str
    kind: OperationKind
    status: OperationStatus
    stage: OperationStage | None
    subject_operation_id: UUID | None
    target_resource_id: UUID | None
    configuration_digest: str
    updated_at: datetime

    def __post_init__(self) -> None:
        for value in (self.operation_id, self.channel_public_id, self.owner_identity_public_id):
            _require_uuid(value, "current operation reference")
        for value in (self.subject_operation_id, self.target_resource_id):
            _require_optional_uuid(value, "current operation relation")
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("invalid current provider")
        if not isinstance(self.kind, OperationKind) or not isinstance(self.status, OperationStatus):
            raise ValueError("invalid current operation state")
        if self.stage is not None and not isinstance(self.stage, OperationStage):
            raise ValueError("invalid current operation stage")
        _require_sha256(self.configuration_digest, "configuration comparison digest")
        _require_aware_datetime(self.updated_at, "current operation updated at")

    def __repr__(self) -> str:
        return "<CurrentOperationSnapshot axes=redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class CurrentReadSnapshot:
    """公開portで取得した停止intentと自appの比較値。不存在も比較対象。"""

    state: CurrentStateSnapshot | None
    resources: tuple[CurrentResourceSnapshot, ...]
    operations: tuple[CurrentOperationSnapshot, ...]
    deactivation: DeactivationView | None
    configuration: CurrentConfiguration
    release_target_id: UUID | None
    recovery_targets: tuple[RecoveryTarget, ...]

    def __post_init__(self) -> None:
        if self.state is not None and not isinstance(self.state, CurrentStateSnapshot):
            raise ValueError("invalid current state snapshot")
        _require_tuple_of(self.resources, CurrentResourceSnapshot, "current resource snapshots")
        _require_tuple_of(self.operations, CurrentOperationSnapshot, "current operation snapshots")
        if self.deactivation is not None and not isinstance(self.deactivation, DeactivationView):
            raise ValueError("invalid current deactivation")
        if not isinstance(self.configuration, (ConfigurationAvailable, ConfigurationUnavailable)):
            raise ValueError("invalid snapshot configuration")
        _require_optional_uuid(self.release_target_id, "release target")
        _require_tuple_of(self.recovery_targets, RecoveryTarget, "recovery targets")

    def __repr__(self) -> str:
        return "<CurrentReadSnapshot axes=redacted configuration=redacted>"


@dataclass(frozen=True, slots=True)
class CurrentReadMatched:
    snapshot: CurrentReadSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, CurrentReadSnapshot):
            raise ValueError("invalid matched snapshot")


@dataclass(frozen=True, slots=True)
class CurrentReadConflict:
    code: SafeResultCode = SafeResultCode.CURRENT_CHANGED

    def __post_init__(self) -> None:
        if self.code is not SafeResultCode.CURRENT_CHANGED:
            raise ValueError("invalid current conflict")


@dataclass(frozen=True, slots=True, repr=False)
class CurrentMenuSucceeded:
    channel_public_id: UUID
    channel_revision: datetime
    checked_at: datetime | None
    rich: ChannelStateView
    deactivation: DeactivationView | None
    current: CurrentContent
    recovery_targets: tuple[RecoveryTarget, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.channel_public_id, "current channel")
        _require_aware_datetime(self.channel_revision, "current channel revision")
        if not isinstance(self.current, CurrentContent):
            raise ValueError("invalid current content")
        if self.current.kind == "inactive":
            if self.checked_at is not None:
                raise ValueError("inactive channel cannot have a current observation")
        else:
            _require_aware_datetime(self.checked_at, "current checked at")
        if (
            not isinstance(self.rich, ChannelStateView)
            or self.rich.channel_public_id != self.channel_public_id
        ):
            raise ValueError("invalid current channel state")
        if self.deactivation is not None and (
            not isinstance(self.deactivation, DeactivationView)
            or self.deactivation.channel_public_id != self.channel_public_id
            or self.deactivation.channel_revision != self.channel_revision
        ):
            raise ValueError("invalid current deactivation scope")
        _require_tuple_of(self.recovery_targets, RecoveryTarget, "current recovery targets")

    def __repr__(self) -> str:
        return "<CurrentMenuSucceeded content=redacted>"


class CurrentReadRepository(Protocol):
    def capture_current(self, scope: OwnerChannelScope) -> CurrentReadSnapshot: ...

    def lock_current_unchanged(
        self,
        scope: OwnerChannelScope,
        expected: CurrentReadSnapshot,
    ) -> CurrentReadMatched | CurrentReadConflict: ...


class DeactivationReadPort(Protocol):
    """既存get_for_ownerと公開値をcontainerで注入するための構造的port。"""

    def get_for_owner(
        self,
        owner_identity_public_id: UUID,
        provider_id: str,
        channel_public_id: UUID,
    ) -> DeactivationView | DeactivationConflict | None: ...
