from __future__ import annotations

from dataclasses import dataclass

from .types import OperationKind, OperationStage, OperationStatus, ResourceLifecycle


class InvalidStateTransition(ValueError):
    """Raised before persistence when a requested transition is not allowed."""


@dataclass(frozen=True, slots=True)
class OperationState:
    status: OperationStatus
    stage: OperationStage | None


_TERMINAL_OPERATION_STATUSES = frozenset(
    {OperationStatus.FAILED, OperationStatus.SUCCEEDED}
)
_INITIAL_STAGE_BY_KIND = {
    OperationKind.APPLY: OperationStage.CREATING,
    OperationKind.UNLINK: OperationStage.CLEARING_DEFAULT,
    OperationKind.RELEASE: OperationStage.LOCAL_RELEASE,
}
_RECOVERY_STAGE_BY_KIND = {
    OperationKind.RECHECK: OperationStage.VERIFYING,
    OperationKind.CLEANUP: OperationStage.CLEANING,
}
_PROCESSING_SUCCESSORS = {
    (OperationKind.APPLY, OperationStage.CREATING): OperationStage.UPLOADING,
    (OperationKind.APPLY, OperationStage.UPLOADING): OperationStage.SETTING_DEFAULT,
    (OperationKind.APPLY, OperationStage.SETTING_DEFAULT): OperationStage.VERIFYING,
    (OperationKind.UNLINK, OperationStage.CLEARING_DEFAULT): OperationStage.VERIFYING,
}
_PROCESSING_OUTCOMES = {
    (OperationKind.APPLY, OperationStage.CREATING): frozenset(
        {OperationStatus.FAILED, OperationStatus.UNKNOWN}
    ),
    (OperationKind.APPLY, OperationStage.UPLOADING): frozenset(
        {OperationStatus.UNKNOWN, OperationStatus.CLEANUP_REQUIRED}
    ),
    (OperationKind.APPLY, OperationStage.SETTING_DEFAULT): frozenset(
        {OperationStatus.UNKNOWN, OperationStatus.CLEANUP_REQUIRED}
    ),
    (OperationKind.APPLY, OperationStage.VERIFYING): frozenset(
        {OperationStatus.SUCCEEDED, OperationStatus.UNKNOWN, OperationStatus.CLEANUP_REQUIRED}
    ),
    (OperationKind.UNLINK, OperationStage.CLEARING_DEFAULT): frozenset(
        {OperationStatus.FAILED, OperationStatus.UNKNOWN}
    ),
    (OperationKind.UNLINK, OperationStage.VERIFYING): frozenset(
        {OperationStatus.SUCCEEDED, OperationStatus.FAILED, OperationStatus.UNKNOWN}
    ),
    (OperationKind.RELEASE, OperationStage.LOCAL_RELEASE): frozenset(
        {OperationStatus.SUCCEEDED, OperationStatus.FAILED}
    ),
}
_ALLOWED_RESOURCE_EDGES = frozenset(
    {
        (ResourceLifecycle.CANDIDATE, ResourceLifecycle.APPLIED),
        (ResourceLifecycle.CANDIDATE, ResourceLifecycle.CLEANUP_REQUIRED),
        (ResourceLifecycle.APPLIED, ResourceLifecycle.OLD),
        (ResourceLifecycle.APPLIED, ResourceLifecycle.CLEANUP_REQUIRED),
        (ResourceLifecycle.APPLIED, ResourceLifecycle.RELEASED),
        (ResourceLifecycle.OLD, ResourceLifecycle.CLEANUP_REQUIRED),
        (ResourceLifecycle.OLD, ResourceLifecycle.DELETED),
        (ResourceLifecycle.CLEANUP_REQUIRED, ResourceLifecycle.DELETED),
    }
)


def transition_operation(
    *,
    kind: OperationKind,
    current_status: OperationStatus,
    current_stage: OperationStage | None,
    next_status: OperationStatus,
    next_stage: OperationStage,
) -> OperationState:
    if not isinstance(kind, OperationKind) or not all(
        isinstance(value, expected)
        for value, expected in (
            (current_status, OperationStatus),
            (next_status, OperationStatus),
            (next_stage, OperationStage),
        )
    ):
        raise InvalidStateTransition("invalid operation state")
    if current_stage is not None and not isinstance(current_stage, OperationStage):
        raise InvalidStateTransition("invalid operation stage")
    if current_status in _TERMINAL_OPERATION_STATUSES:
        raise InvalidStateTransition("terminal operation")
    if current_status is OperationStatus.ACCEPTED:
        expected_stage = _INITIAL_STAGE_BY_KIND.get(kind)
        expected_status = OperationStatus.PROCESSING
        if kind in _RECOVERY_STAGE_BY_KIND:
            expected_stage = _RECOVERY_STAGE_BY_KIND[kind]
            expected_status = OperationStatus.RECOVERY_ACTIVE
        if (
            current_stage is not None
            or next_status is not expected_status
            or next_stage is not expected_stage
        ):
            raise InvalidStateTransition("invalid initial operation stage")
    elif current_status is OperationStatus.PROCESSING:
        if kind not in _INITIAL_STAGE_BY_KIND:
            raise InvalidStateTransition("invalid processing operation kind")
        if next_status is OperationStatus.PROCESSING:
            if _PROCESSING_SUCCESSORS.get((kind, current_stage)) is not next_stage:
                raise InvalidStateTransition("invalid operation stage progression")
        elif next_status in _PROCESSING_OUTCOMES.get((kind, current_stage), frozenset()):
            expected_result_stage = (
                OperationStage.CLEANING
                if next_status is OperationStatus.CLEANUP_REQUIRED
                else current_stage
            )
            if next_stage is not expected_result_stage:
                raise InvalidStateTransition("invalid cleanup transition")
        else:
            raise InvalidStateTransition("invalid operation status transition")
    elif current_status is OperationStatus.RECOVERY_ACTIVE:
        expected_stage = _RECOVERY_STAGE_BY_KIND.get(kind)
        if (
            expected_stage is None
            or current_stage is not expected_stage
            or next_stage is not current_stage
            or next_status not in {
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.UNKNOWN,
                OperationStatus.CLEANUP_REQUIRED,
            }
        ):
            raise InvalidStateTransition("invalid recovery transition")
    elif next_status in {OperationStatus.UNKNOWN, OperationStatus.FAILED}:
        if next_stage is not current_stage:
            raise InvalidStateTransition("failure must preserve operation stage")
    else:
        raise InvalidStateTransition("invalid operation status transition")

    return OperationState(status=next_status, stage=next_stage)


def transition_resource(
    current: ResourceLifecycle, next_lifecycle: ResourceLifecycle
) -> ResourceLifecycle:
    if not isinstance(current, ResourceLifecycle) or not isinstance(
        next_lifecycle, ResourceLifecycle
    ):
        raise InvalidStateTransition("invalid resource lifecycle")
    if (current, next_lifecycle) not in _ALLOWED_RESOURCE_EDGES:
        raise InvalidStateTransition("invalid resource lifecycle transition")
    return next_lifecycle
