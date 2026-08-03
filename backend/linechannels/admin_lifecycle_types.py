from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


DeactivationStatus = Literal[
    "checking", "unlinking", "confirmation_required", "completed"
]


@dataclass(frozen=True, slots=True)
class DeactivationSummary:
    operation_id: UUID
    status: DeactivationStatus
    safe_reason: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DeactivationView:
    channel_public_id: UUID
    channel_active: bool
    channel_revision: datetime
    operation_id: UUID
    expected_channel_revision: datetime
    status: DeactivationStatus
    safe_reason: str | None
    subject_rich_operation_id: UUID | None
    latest_recovery_operation_id: UUID | None
    accepted_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    recovery_result_ready: bool = False


@dataclass(frozen=True, slots=True)
class ReserveDeactivation:
    owner_identity_public_id: UUID
    provider_id: str
    channel_public_id: UUID
    operation_id: UUID
    expected_channel_revision: datetime


@dataclass(frozen=True, slots=True)
class ReservedDeactivation:
    view: DeactivationView
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class DeactivationConflict:
    code: str


@dataclass(frozen=True, slots=True)
class SaveDeactivationResult:
    owner_identity_public_id: UUID
    provider_id: str
    channel_public_id: UUID
    operation_id: UUID
    expected_channel_revision: datetime
    status: Literal["unlinking", "confirmation_required"]
    safe_reason: str | None = None
    subject_rich_operation_id: UUID | None = None
    recovery_operation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SavedDeactivation:
    view: DeactivationView


@dataclass(frozen=True, slots=True)
class RecordDeactivationRevisionConflict:
    owner_identity_public_id: UUID
    provider_id: str
    channel_public_id: UUID
    operation_id: UUID


@dataclass(frozen=True, slots=True)
class AdvanceDeactivationRevision:
    owner_identity_public_id: UUID
    provider_id: str
    channel_public_id: UUID
    operation_id: UUID
    recovery_operation_id: UUID
    presented_channel_revision: datetime


@dataclass(frozen=True, slots=True)
class LockedDeactivation:
    view: DeactivationView


@dataclass(frozen=True, slots=True)
class CompleteDeactivation:
    owner_identity_public_id: UUID
    provider_id: str
    channel_public_id: UUID
    operation_id: UUID
    expected_channel_revision: datetime


@dataclass(frozen=True, slots=True)
class CompletedDeactivation:
    view: DeactivationView
    channel_revision: datetime


@dataclass(frozen=True, slots=True)
class StartDeactivation:
    channel_public_id: UUID
    operation_id: UUID
    expected_channel_revision: datetime


@dataclass(frozen=True, slots=True)
class RecheckDeactivation:
    channel_public_id: UUID
    operation_id: UUID
    recovery_operation_id: UUID
    expected_channel_revision: datetime


@dataclass(frozen=True, slots=True)
class ReactivateChannel:
    channel_public_id: UUID
    expected_channel_revision: datetime
    repair_credentials: object | None = None


@dataclass(frozen=True, slots=True)
class DeactivationSucceeded:
    view: DeactivationView | None
    status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True, slots=True)
class DeactivationFailed:
    code: str
    status: Literal["failed"] = "failed"


@dataclass(frozen=True, slots=True)
class ReactivationSucceeded:
    channel_public_id: UUID
    channel_revision: datetime
    provider_id: str
    refresh_required: Literal[True] = True
    status: Literal["succeeded"] = "succeeded"
