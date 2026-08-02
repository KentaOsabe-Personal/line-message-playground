from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Mapping, Protocol
from uuid import UUID

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from linechannels.reference_fence import ChannelReferenceFence, DjangoChannelReferenceFence

from .models import (
    ManagedRichMenu,
    RichMenuChannelState,
    RichMenuOperation,
    RichMenuOperationTransition,
)
from .reconciliation import ManagedResourceTarget
from .state_machine import InvalidStateTransition, transition_operation, transition_resource
from .types import (
    NextAllowedAction,
    ChannelStateView,
    CleanupRelation,
    DefaultObservation,
    DefaultRelation,
    HistoryEntry,
    HistoryPage,
    HistorySummary,
    ManagedResourceView,
    NormalizedTemplate,
    ObservationKind,
    OperationKind,
    OperationStage,
    OperationStatus,
    OperationView,
    ResourceLifecycle,
    SafeResultCode,
    TemplateFieldValue,
    TemplateReference,
)


@dataclass(frozen=True, slots=True)
class OperationFenceSnapshot:
    owner_identity_public_id: UUID
    provider_id: str
    channel_public_id: UUID
    expected_channel_revision: datetime


@dataclass(frozen=True, slots=True)
class OperationFenceResult:
    status: str

    def __post_init__(self) -> None:
        if self.status not in {"matched", "stale", "unavailable"}:
            raise ValueError("invalid operation fence result")


class OperationFence(Protocol):
    def lock_exact(self, snapshot: OperationFenceSnapshot) -> OperationFenceResult: ...


class UnavailableOperationFence:
    def lock_exact(self, snapshot: OperationFenceSnapshot) -> OperationFenceResult:
        del snapshot
        return OperationFenceResult("unavailable")


@dataclass(frozen=True, slots=True)
class StageClaimed:
    operation: OperationView
    fence: OperationFenceSnapshot


@dataclass(frozen=True, slots=True)
class StageConflict:
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in {"operation_not_found", "stage_in_flight", "stale_stage", "invalid_transition"}:
            raise ValueError("invalid stage conflict")


@dataclass(frozen=True, slots=True)
class StageExpired:
    operation: OperationView


@dataclass(frozen=True, slots=True)
class StageOutcome:
    operation_id: UUID
    expected_stage: OperationStage
    next_status: OperationStatus
    next_stage: OperationStage
    result: SafeResultCode

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, UUID):
            raise ValueError("invalid operation id")
        if not isinstance(self.expected_stage, OperationStage):
            raise ValueError("invalid expected stage")
        if not isinstance(self.next_status, OperationStatus):
            raise ValueError("invalid next status")
        if not isinstance(self.next_stage, OperationStage):
            raise ValueError("invalid next stage")
        if not isinstance(self.result, SafeResultCode):
            raise ValueError("invalid stage result")


@dataclass(frozen=True, slots=True)
class RecoveryAccepted:
    operation: OperationView


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    recovery_operation_id: UUID
    subject_operation_id: UUID
    subject_next_status: OperationStatus
    subject_next_stage: OperationStage
    subject_result: SafeResultCode
    blocker_moves_to_recovery: bool

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_operation_id, UUID) or not isinstance(
            self.subject_operation_id, UUID
        ):
            raise ValueError("invalid recovery relation")
        if not isinstance(self.subject_next_status, OperationStatus) or not isinstance(
            self.subject_next_stage, OperationStage
        ):
            raise ValueError("invalid subject state")
        if not isinstance(self.subject_result, SafeResultCode):
            raise ValueError("invalid subject result")
        if type(self.blocker_moves_to_recovery) is not bool:
            raise ValueError("invalid blocker handoff")


@dataclass(frozen=True, slots=True)
class RecoveryHandoffResult:
    recovery: OperationView
    subject: OperationView


@dataclass(frozen=True, slots=True)
class ReplacementRecorded:
    current_resource: ManagedResourceView
    old_resource: ManagedResourceView


@dataclass(frozen=True, slots=True)
class OwnerChannelScope:
    owner_identity_public_id: UUID
    provider_id: str
    channel_public_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.owner_identity_public_id, UUID) or not isinstance(
            self.channel_public_id, UUID
        ):
            raise ValueError("invalid owner channel scope")
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("invalid provider scope")


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    scope: OwnerChannelScope
    limit: int
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, OwnerChannelScope):
            raise ValueError("invalid history scope")
        if type(self.limit) is not int or not 1 <= self.limit <= 50:
            raise ValueError("invalid history limit")
        if self.cursor is not None and (
            not isinstance(self.cursor, str) or not self.cursor
        ):
            raise ValueError("invalid history cursor")


@dataclass(frozen=True, slots=True)
class AcceptedOperation:
    operation_id: UUID
    channel_public_id: UUID
    owner_identity_public_id: UUID
    provider_id: str
    expected_channel_revision: datetime
    kind: OperationKind
    subject_operation_id: UUID | None
    target_resource_id: UUID | None
    request_fingerprint: str
    confirmation_usage_digest: str | None = None
    configuration_snapshot: Mapping[str, object] | None = None
    candidate_image_digest: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.operation_id,
            self.channel_public_id,
            self.owner_identity_public_id,
        ):
            if not isinstance(value, UUID):
                raise ValueError("invalid operation command")
        if not isinstance(self.kind, OperationKind):
            raise ValueError("invalid operation kind")
        if not self.provider_id or timezone.is_naive(self.expected_channel_revision):
            raise ValueError("invalid operation scope")
        _require_digest(self.request_fingerprint)
        if self.confirmation_usage_digest is not None:
            _require_digest(self.confirmation_usage_digest)
        if self.candidate_image_digest is not None:
            _require_digest(self.candidate_image_digest)
        relation = (
            self.subject_operation_id is not None,
            self.target_resource_id is not None,
        )
        expected = {
            OperationKind.APPLY: (False, False),
            OperationKind.UNLINK: (False, True),
            OperationKind.RELEASE: (False, True),
            OperationKind.RECHECK: (True, False),
            OperationKind.CLEANUP: (True, True),
        }[self.kind]
        if relation != expected:
            raise ValueError("invalid operation relation")
        if self.kind is OperationKind.APPLY:
            if (
                self.confirmation_usage_digest is None
                or self.configuration_snapshot is None
                or self.candidate_image_digest is None
            ):
                raise ValueError("apply reservation data required")
        elif self.candidate_image_digest is not None:
            raise ValueError("candidate only allowed for apply")


@dataclass(frozen=True, slots=True)
class OperationAccepted:
    operation: OperationView
    candidate_resource_id: UUID | None


@dataclass(frozen=True, slots=True)
class OperationReplay:
    operation: OperationView


@dataclass(frozen=True, slots=True)
class OperationConflict:
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in {
            "operation_conflict",
            "operation_in_progress",
            "confirmation_used",
            "channel_unavailable",
            "storage_retryable",
            "storage_unavailable",
            "invalid_relation",
            "stale_channel",
        }:
            raise ValueError("invalid operation conflict")


class DjangoRichMenuRepository:
    def __init__(
        self,
        *,
        reference_fence: ChannelReferenceFence | None = None,
        operation_fence: OperationFence | None = None,
        using: str = "default",
        clock: Callable[[], datetime] = timezone.now,
        in_flight_timeout: timedelta = timedelta(minutes=5),
    ) -> None:
        self.using = using
        self._reference_fence = reference_fence or DjangoChannelReferenceFence(using=using)
        self._operation_fence = operation_fence or UnavailableOperationFence()
        self._clock = clock
        if not isinstance(in_flight_timeout, timedelta) or in_flight_timeout.total_seconds() <= 0:
            raise ValueError("invalid in-flight timeout")
        self._in_flight_timeout = in_flight_timeout

    def claim_stage(
        self, operation_id: UUID, expected_stage: OperationStage
    ) -> StageClaimed | StageExpired | StageConflict:
        if not isinstance(operation_id, UUID) or not isinstance(expected_stage, OperationStage):
            raise TypeError("invalid stage claim")
        with transaction.atomic(using=self.using):
            operation = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(operation_id=operation_id)
                .first()
            )
            if operation is None:
                return StageConflict("operation_not_found")
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=operation.channel_state_id
            )
            if operation.stage_started_at is not None:
                if self._clock() - operation.stage_started_at >= self._in_flight_timeout:
                    if operation.status == OperationStatus.RECOVERY_ACTIVE.value:
                        if (
                            operation.stage != expected_stage.value
                            or operation.subject_operation_id is None
                            or state.active_operation_id != operation.operation_id
                            or state.blocking_operation_id != operation.subject_operation_id
                        ):
                            return StageConflict("stale_stage")
                        return StageExpired(
                            self._expire_recovery(operation=operation, state=state)
                        )
                    return StageExpired(self._make_unknown(operation=operation, state=state))
                return StageConflict("stage_in_flight")
            current_status = OperationStatus(operation.status)
            current_stage = None if operation.stage is None else OperationStage(operation.stage)
            if current_status is OperationStatus.ACCEPTED:
                try:
                    next_state = transition_operation(
                        kind=OperationKind(operation.kind),
                        current_status=current_status,
                        current_stage=current_stage,
                        next_status=OperationStatus.PROCESSING,
                        next_stage=expected_stage,
                    )
                except InvalidStateTransition:
                    return StageConflict("invalid_transition")
                self._append_transition(
                    operation=operation,
                    from_status=current_status,
                    to_status=next_state.status,
                    stage=next_state.stage,
                    reason=SafeResultCode.ACCEPTED,
                )
                operation.status = next_state.status.value
                operation.stage = next_state.stage.value
            elif current_status is OperationStatus.PROCESSING and current_stage is expected_stage:
                pass
            else:
                return StageConflict("stale_stage")
            operation.stage_started_at = self._clock()
            operation.save(
                using=self.using,
                update_fields=("status", "stage", "stage_started_at", "updated_at"),
            )
            if state.active_operation_id != operation.operation_id:
                state.active_operation = operation
                state.save(using=self.using, update_fields=("active_operation", "updated_at"))
            return StageClaimed(_operation_view(operation), _fence_snapshot(operation))

    def complete_stage(
        self, outcome: StageOutcome
    ) -> OperationView | StageConflict:
        if not isinstance(outcome, StageOutcome):
            raise TypeError("invalid stage outcome")
        with transaction.atomic(using=self.using):
            operation = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(operation_id=outcome.operation_id)
                .first()
            )
            if operation is None:
                return StageConflict("operation_not_found")
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=operation.channel_state_id
            )
            if (
                operation.status != OperationStatus.PROCESSING.value
                or operation.stage != outcome.expected_stage.value
                or operation.stage_started_at is None
            ):
                return StageConflict("stale_stage")

            fence = self._operation_fence.lock_exact(_fence_snapshot(operation))
            if fence.status != "matched":
                return self._make_unknown(operation=operation, state=state)

            try:
                next_state = transition_operation(
                    kind=OperationKind(operation.kind),
                    current_status=OperationStatus(operation.status),
                    current_stage=OperationStage(operation.stage),
                    next_status=outcome.next_status,
                    next_stage=outcome.next_stage,
                )
            except InvalidStateTransition:
                return StageConflict("invalid_transition")
            self._append_transition(
                operation=operation,
                from_status=OperationStatus(operation.status),
                to_status=next_state.status,
                stage=next_state.stage,
                reason=outcome.result,
            )
            operation.status = next_state.status.value
            operation.stage = next_state.stage.value
            operation.stage_started_at = None
            operation.result_code = outcome.result.value
            if next_state.status in {OperationStatus.FAILED, OperationStatus.SUCCEEDED}:
                operation.completed_at = self._clock()
                state.active_operation = None
            elif next_state.status in {OperationStatus.UNKNOWN, OperationStatus.CLEANUP_REQUIRED}:
                state.blocking_operation = operation
                state.active_operation = None
            operation.save(
                using=self.using,
                update_fields=("status", "stage", "stage_started_at", "result_code", "completed_at", "updated_at"),
            )
            state.save(
                using=self.using,
                update_fields=("blocking_operation", "active_operation", "updated_at"),
            )
            return _operation_view(operation)

    def _make_unknown(self, *, operation: RichMenuOperation, state: RichMenuChannelState) -> OperationView:
        self._append_transition(
            operation=operation,
            from_status=OperationStatus.PROCESSING,
            to_status=OperationStatus.UNKNOWN,
            stage=OperationStage(operation.stage),
            reason=SafeResultCode.RESPONSE_UNKNOWN,
        )
        operation.status = OperationStatus.UNKNOWN.value
        operation.stage_started_at = None
        operation.result_code = SafeResultCode.RESPONSE_UNKNOWN.value
        operation.save(
            using=self.using,
            update_fields=("status", "stage_started_at", "result_code", "updated_at"),
        )
        state.blocking_operation = operation
        state.active_operation = None
        state.save(
            using=self.using,
            update_fields=("blocking_operation", "active_operation", "updated_at"),
        )
        return _operation_view(operation)

    def _expire_recovery(
        self, *, operation: RichMenuOperation, state: RichMenuChannelState
    ) -> OperationView:
        kind = OperationKind(operation.kind)
        next_status = (
            OperationStatus.FAILED
            if kind is OperationKind.RECHECK
            else OperationStatus.UNKNOWN
        )
        self._append_transition(
            operation=operation,
            from_status=OperationStatus.RECOVERY_ACTIVE,
            to_status=next_status,
            stage=OperationStage(operation.stage),
            reason=SafeResultCode.RESPONSE_UNKNOWN,
        )
        operation.status = next_status.value
        operation.stage_started_at = None
        operation.result_code = SafeResultCode.RESPONSE_UNKNOWN.value
        if next_status is OperationStatus.FAILED:
            operation.completed_at = self._clock()
        operation.save(
            using=self.using,
            update_fields=(
                "status",
                "stage_started_at",
                "result_code",
                "completed_at",
                "updated_at",
            ),
        )
        state.active_operation = None
        if kind is OperationKind.CLEANUP:
            state.blocking_operation = operation
        state.save(
            using=self.using,
            update_fields=("blocking_operation", "active_operation", "updated_at"),
        )
        return _operation_view(operation)

    def _append_transition(
        self,
        *,
        operation: RichMenuOperation,
        from_status: OperationStatus,
        to_status: OperationStatus,
        stage: OperationStage,
        reason: SafeResultCode,
    ) -> None:
        last_sequence = (
            RichMenuOperationTransition.objects.using(self.using)
            .filter(operation=operation)
            .order_by("-sequence")
            .values_list("sequence", flat=True)
            .first()
        )
        RichMenuOperationTransition.objects.using(self.using).create(
            operation=operation,
            sequence=(last_sequence or 0) + 1,
            from_status=from_status.value,
            to_status=to_status.value,
            stage=stage.value,
            safe_reason=reason.value,
            observed_at=self._clock(),
        )

    def accept(
        self, command: AcceptedOperation
    ) -> OperationAccepted | OperationReplay | OperationConflict:
        if not isinstance(command, AcceptedOperation):
            raise TypeError("invalid operation command")
        with transaction.atomic(using=self.using):
            fence = self._reference_fence.lock_existing(command.channel_public_id)
            if fence.status != "locked":
                return OperationConflict(
                    "channel_unavailable"
                    if fence.status == "channel_not_found"
                    else fence.status
                )

            existing = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .filter(operation_id=command.operation_id)
                .first()
            )
            if existing is not None:
                if existing.request_fingerprint != command.request_fingerprint:
                    return OperationConflict("operation_conflict")
                return OperationReplay(_operation_view(existing))

            state, _ = RichMenuChannelState.objects.using(self.using).get_or_create(
                channel_public_id=command.channel_public_id
            )
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=state.channel_public_id
            )
            target = None
            if command.kind in {OperationKind.UNLINK, OperationKind.RELEASE}:
                target = (
                    ManagedRichMenu.objects.using(self.using)
                    .select_for_update()
                    .filter(
                        public_id=command.target_resource_id,
                        channel_state=state,
                        lifecycle=ResourceLifecycle.APPLIED.value,
                    )
                    .first()
                )
                if target is None or state.current_resource_id != target.public_id:
                    return OperationConflict("invalid_relation")
            if command.confirmation_usage_digest is not None and (
                RichMenuOperation.objects.using(self.using)
                .filter(confirmation_usage_digest=command.confirmation_usage_digest)
                .exists()
            ):
                return OperationConflict("confirmation_used")
            if state.active_operation_id is not None or state.blocking_operation_id is not None:
                return OperationConflict("operation_in_progress")

            now = self._clock()
            try:
                with transaction.atomic(using=self.using):
                    operation = RichMenuOperation.objects.using(self.using).create(
                        operation_id=command.operation_id,
                        channel_state=state,
                        owner_identity_public_id=command.owner_identity_public_id,
                        provider_id=command.provider_id,
                        kind=command.kind.value,
                        target_resource=target,
                        request_fingerprint=command.request_fingerprint,
                        confirmation_usage_digest=command.confirmation_usage_digest,
                        expected_channel_revision=command.expected_channel_revision,
                        status=OperationStatus.ACCEPTED.value,
                        stage=None,
                        result_code=SafeResultCode.ACCEPTED.value,
                        configuration_snapshot=(
                            None
                            if command.configuration_snapshot is None
                            else json.loads(json.dumps(command.configuration_snapshot))
                        ),
                        accepted_at=now,
                    )
            except IntegrityError:
                replay = RichMenuOperation.objects.using(self.using).filter(
                    operation_id=command.operation_id
                ).first()
                if replay is not None and replay.request_fingerprint == command.request_fingerprint:
                    return OperationReplay(_operation_view(replay))
                return OperationConflict(
                    "confirmation_used"
                    if command.confirmation_usage_digest is not None
                    else "operation_conflict"
                )

            candidate_id = None
            if command.kind is OperationKind.APPLY:
                candidate = ManagedRichMenu.objects.using(self.using).create(
                    channel_state=state,
                    origin_operation=operation,
                    ownership_marker=f"lrm:v1:{secrets.token_hex(16)}",
                    lifecycle="candidate",
                    image_digest=command.candidate_image_digest,
                )
                candidate_id = candidate.public_id

            state.active_operation = operation
            state.save(using=self.using, update_fields=("active_operation", "updated_at"))
            return OperationAccepted(_operation_view(operation), candidate_id)

    def list_managed_resources(
        self, scope: OwnerChannelScope
    ) -> tuple[ManagedResourceTarget, ...]:
        if not isinstance(scope, OwnerChannelScope):
            raise TypeError("invalid owner channel scope")
        resources = (
            ManagedRichMenu.objects.using(self.using)
            .select_related("origin_operation")
            .filter(
                channel_state_id=scope.channel_public_id,
                origin_operation__owner_identity_public_id=scope.owner_identity_public_id,
                origin_operation__provider_id=scope.provider_id,
            )
            .order_by("created_at", "public_id")
        )
        return tuple(_resource_target(resource) for resource in resources)

    def record_observation(
        self, scope: OwnerChannelScope, observation: DefaultObservation
    ) -> bool:
        if not isinstance(scope, OwnerChannelScope) or not isinstance(
            observation, DefaultObservation
        ):
            raise TypeError("invalid observation")
        with transaction.atomic(using=self.using):
            state, _ = RichMenuChannelState.objects.using(self.using).get_or_create(
                channel_public_id=scope.channel_public_id
            )
            state = (
                RichMenuChannelState.objects.using(self.using)
                .select_for_update()
                .get(channel_public_id=scope.channel_public_id)
            )
            if observation.managed_resource_id is not None:
                owned = (
                    ManagedRichMenu.objects.using(self.using)
                    .select_related("origin_operation")
                    .filter(
                        public_id=observation.managed_resource_id,
                        channel_state_id=scope.channel_public_id,
                        origin_operation__owner_identity_public_id=scope.owner_identity_public_id,
                        origin_operation__provider_id=scope.provider_id,
                    )
                    .exists()
                )
                if not owned:
                    return False
            state.last_observation_kind = observation.kind.value
            state.last_observation_fingerprint = observation.fingerprint
            state.last_observed_at = observation.observed_at
            state.save(
                using=self.using,
                update_fields=(
                    "last_observation_kind",
                    "last_observation_fingerprint",
                    "last_observed_at",
                    "updated_at",
                ),
            )
        return True

    def get_managed_resource(
        self, scope: OwnerChannelScope, resource_id: UUID
    ) -> ManagedResourceTarget | None:
        if not isinstance(scope, OwnerChannelScope) or not isinstance(resource_id, UUID):
            raise TypeError("invalid managed resource query")
        resource = (
            ManagedRichMenu.objects.using(self.using)
            .select_related("origin_operation", "replacement_operation")
            .filter(
                public_id=resource_id,
                channel_state_id=scope.channel_public_id,
                origin_operation__owner_identity_public_id=scope.owner_identity_public_id,
                origin_operation__provider_id=scope.provider_id,
            )
            .first()
        )
        return None if resource is None else _resource_target(resource)

    def get_candidate_for_operation(
        self, scope: OwnerChannelScope, operation_id: UUID
    ) -> ManagedResourceTarget | None:
        if not isinstance(scope, OwnerChannelScope) or not isinstance(operation_id, UUID):
            raise TypeError("invalid candidate query")
        resources = tuple(
            ManagedRichMenu.objects.using(self.using)
            .select_related("origin_operation", "replacement_operation")
            .filter(
                origin_operation_id=operation_id,
                channel_state_id=scope.channel_public_id,
                origin_operation__owner_identity_public_id=scope.owner_identity_public_id,
                origin_operation__provider_id=scope.provider_id,
            )
            .order_by("created_at", "public_id")
        )
        if len(resources) != 1:
            return None
        return _resource_target(resources[0])

    def bind_resource_line_id(self, resource_id: UUID, line_rich_menu_id: str) -> bool:
        if not isinstance(resource_id, UUID) or not isinstance(line_rich_menu_id, str) or not line_rich_menu_id:
            raise TypeError("invalid line resource binding")
        with transaction.atomic(using=self.using):
            resource = (
                ManagedRichMenu.objects.using(self.using)
                .select_for_update()
                .filter(public_id=resource_id)
                .first()
            )
            if resource is None:
                return False
            if resource.line_rich_menu_id is not None:
                return resource.line_rich_menu_id == line_rich_menu_id
            resource.line_rich_menu_id = line_rich_menu_id
            try:
                resource.save(using=self.using, update_fields=("line_rich_menu_id", "updated_at"))
            except IntegrityError:
                return False
        return True

    def get_operation_by_id(self, operation_id: UUID) -> OperationView | None:
        if not isinstance(operation_id, UUID):
            raise TypeError("invalid operation id")
        operation = (
            RichMenuOperation.objects.using(self.using)
            .select_related("channel_state")
            .filter(operation_id=operation_id)
            .first()
        )
        return None if operation is None else _operation_view(operation)

    def get_operation_for_owner(
        self, owner_identity_public_id: UUID, provider_id: str, operation_id: UUID
    ) -> OperationView | None:
        if not isinstance(owner_identity_public_id, UUID) or not isinstance(operation_id, UUID):
            raise TypeError("invalid operation scope")
        operation = (
            RichMenuOperation.objects.using(self.using)
            .select_related("channel_state")
            .filter(
                operation_id=operation_id,
                owner_identity_public_id=owner_identity_public_id,
                provider_id=provider_id,
            )
            .first()
        )
        return None if operation is None else _operation_view(operation)

    def get_request_fingerprint(self, operation_id: UUID) -> str | None:
        if not isinstance(operation_id, UUID):
            raise TypeError("invalid operation id")
        return (
            RichMenuOperation.objects.using(self.using)
            .filter(operation_id=operation_id)
            .values_list("request_fingerprint", flat=True)
            .first()
        )

    def get_operation_image_digest(self, operation_id: UUID) -> str | None:
        if not isinstance(operation_id, UUID):
            raise TypeError("invalid operation id")
        return (
            ManagedRichMenu.objects.using(self.using)
            .filter(origin_operation_id=operation_id)
            .values_list("image_digest", flat=True)
            .first()
        )

    def mark_resource_cleanup_required(self, resource_id: UUID) -> bool | OperationConflict:
        if not isinstance(resource_id, UUID):
            raise TypeError("invalid resource id")
        with transaction.atomic(using=self.using):
            resource = (
                ManagedRichMenu.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(public_id=resource_id)
                .first()
            )
            if resource is None:
                return OperationConflict("invalid_relation")
            lifecycle = ResourceLifecycle(resource.lifecycle)
            if lifecycle is ResourceLifecycle.CLEANUP_REQUIRED:
                return True
            try:
                next_lifecycle = transition_resource(
                    lifecycle, ResourceLifecycle.CLEANUP_REQUIRED
                )
            except InvalidStateTransition:
                return OperationConflict("invalid_relation")
            resource.lifecycle = next_lifecycle.value
            resource.save(using=self.using, update_fields=("lifecycle", "updated_at"))
            if resource.channel_state.current_resource_id == resource.public_id:
                state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                    channel_public_id=resource.channel_state_id
                )
                state.current_resource = None
                state.save(using=self.using, update_fields=("current_resource", "updated_at"))
        return True

    def discard_candidate(self, resource_id: UUID) -> bool | OperationConflict:
        """外部ID未発行の予約候補だけをローカルで破棄する。"""
        if not isinstance(resource_id, UUID):
            raise TypeError("invalid candidate id")
        with transaction.atomic(using=self.using):
            resource = (
                ManagedRichMenu.objects.using(self.using)
                .select_for_update()
                .filter(public_id=resource_id)
                .first()
            )
            if (
                resource is None
                or resource.lifecycle != ResourceLifecycle.CANDIDATE.value
                or resource.line_rich_menu_id is not None
            ):
                return OperationConflict("invalid_relation")
            resource.lifecycle = ResourceLifecycle.DELETED.value
            resource.deleted_at = self._clock()
            resource.save(using=self.using, update_fields=("lifecycle", "deleted_at", "updated_at"))
        return True

    def finalize_apply(
        self, operation_id: UUID, candidate_resource_id: UUID
    ) -> bool | OperationConflict:
        if not isinstance(operation_id, UUID) or not isinstance(candidate_resource_id, UUID):
            raise TypeError("invalid apply finalization")
        with transaction.atomic(using=self.using):
            operation = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(operation_id=operation_id)
                .first()
            )
            if operation is None:
                return OperationConflict("operation_not_found")
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=operation.channel_state_id
            )
            candidate = (
                ManagedRichMenu.objects.using(self.using)
                .select_for_update()
                .filter(public_id=candidate_resource_id, channel_state=state)
                .first()
            )
            if (
                operation.kind != OperationKind.APPLY.value
                or operation.status != OperationStatus.PROCESSING.value
                or operation.stage != OperationStage.VERIFYING.value
                or state.active_operation_id != operation.operation_id
                or candidate is None
                or candidate.origin_operation_id != operation.operation_id
                or candidate.lifecycle != ResourceLifecycle.CANDIDATE.value
                or candidate.line_rich_menu_id is None
            ):
                return OperationConflict("invalid_relation")
            current = state.current_resource
            if current is None:
                candidate.lifecycle = ResourceLifecycle.APPLIED.value
                candidate.save(using=self.using, update_fields=("lifecycle", "updated_at"))
                state.current_resource = candidate
                state.save(using=self.using, update_fields=("current_resource", "updated_at"))
                return True
            if current.public_id == candidate.public_id:
                return OperationConflict("invalid_relation")
            if current.lifecycle != ResourceLifecycle.APPLIED.value:
                return OperationConflict("invalid_relation")
            replacement = self.record_replacement(
                replacement_operation_id=operation_id,
                new_resource_id=candidate.public_id,
                old_resource_id=current.public_id,
            )
            if isinstance(replacement, OperationConflict):
                return replacement
        return True

    def finalize_unlink(self, resource_id: UUID) -> bool | OperationConflict:
        if not isinstance(resource_id, UUID):
            raise TypeError("invalid unlink resource")
        with transaction.atomic(using=self.using):
            resource = (
                ManagedRichMenu.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(public_id=resource_id)
                .first()
            )
            if resource is None or resource.lifecycle != ResourceLifecycle.APPLIED.value:
                return OperationConflict("invalid_relation")
            resource.lifecycle = ResourceLifecycle.CLEANUP_REQUIRED.value
            resource.save(using=self.using, update_fields=("lifecycle", "updated_at"))
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=resource.channel_state_id
            )
            if state.current_resource_id == resource.public_id:
                state.current_resource = None
                state.save(using=self.using, update_fields=("current_resource", "updated_at"))
        return True

    def finalize_release(self, resource_id: UUID) -> bool | OperationConflict:
        if not isinstance(resource_id, UUID):
            raise TypeError("invalid release resource")
        with transaction.atomic(using=self.using):
            resource = (
                ManagedRichMenu.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(public_id=resource_id)
                .first()
            )
            if resource is None or resource.lifecycle != ResourceLifecycle.APPLIED.value:
                return OperationConflict("invalid_relation")
            resource.lifecycle = ResourceLifecycle.RELEASED.value
            resource.released_at = self._clock()
            resource.save(using=self.using, update_fields=("lifecycle", "released_at", "updated_at"))
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=resource.channel_state_id
            )
            if state.current_resource_id == resource.public_id:
                state.current_resource = None
                state.save(using=self.using, update_fields=("current_resource", "updated_at"))
        return True

    def finalize_deleted(self, resource_id: UUID) -> bool | OperationConflict:
        if not isinstance(resource_id, UUID):
            raise TypeError("invalid delete resource")
        with transaction.atomic(using=self.using):
            resource = (
                ManagedRichMenu.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(public_id=resource_id)
                .first()
            )
            if resource is None or resource.channel_state.current_resource_id == resource.public_id:
                return OperationConflict("invalid_relation")
            if ResourceLifecycle(resource.lifecycle) not in {
                ResourceLifecycle.CANDIDATE,
                ResourceLifecycle.OLD,
                ResourceLifecycle.CLEANUP_REQUIRED,
            }:
                return OperationConflict("invalid_relation")
            resource.lifecycle = ResourceLifecycle.DELETED.value
            resource.deleted_at = self._clock()
            resource.save(using=self.using, update_fields=("lifecycle", "deleted_at", "updated_at"))
        return True

    def complete_recovery(
        self, operation_id: UUID, next_status: OperationStatus, result: SafeResultCode
    ) -> OperationView | StageConflict:
        if not isinstance(operation_id, UUID) or not isinstance(next_status, OperationStatus):
            raise TypeError("invalid recovery completion")
        with transaction.atomic(using=self.using):
            operation = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(operation_id=operation_id)
                .first()
            )
            if operation is None or operation.status != OperationStatus.RECOVERY_ACTIVE.value:
                return StageConflict("stale_stage")
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=operation.channel_state_id
            )
            if next_status not in {OperationStatus.UNKNOWN, OperationStatus.FAILED}:
                return StageConflict("invalid_transition")
            self._append_transition(
                operation=operation,
                from_status=OperationStatus.RECOVERY_ACTIVE,
                to_status=next_status,
                stage=OperationStage(operation.stage),
                reason=result,
            )
            operation.status = next_status.value
            operation.stage_started_at = None
            operation.result_code = result.value
            if next_status is OperationStatus.FAILED:
                operation.completed_at = self._clock()
            state.active_operation = None
            state.blocking_operation = operation
            operation.save(
                using=self.using,
                update_fields=("status", "stage_started_at", "result_code", "completed_at", "updated_at"),
            )
            state.save(using=self.using, update_fields=("active_operation", "blocking_operation", "updated_at"))
            return _operation_view(operation)

    def complete_cleanup_recovery(
        self, operation_id: UUID, resource_id: UUID | None
    ) -> OperationView | StageConflict:
        if not isinstance(operation_id, UUID) or not isinstance(resource_id, UUID):
            raise TypeError("invalid cleanup completion")
        with transaction.atomic(using=self.using):
            operation = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(operation_id=operation_id, kind=OperationKind.CLEANUP.value)
                .first()
            )
            if operation is None or operation.status != OperationStatus.RECOVERY_ACTIVE.value:
                return StageConflict("stale_stage")
            target = ManagedRichMenu.objects.using(self.using).select_for_update().filter(
                public_id=resource_id,
                channel_state_id=operation.channel_state_id,
                lifecycle=ResourceLifecycle.DELETED.value,
            ).first()
            if target is None:
                return StageConflict("invalid_transition")
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=operation.channel_state_id
            )
            self._append_transition(
                operation=operation,
                from_status=OperationStatus.RECOVERY_ACTIVE,
                to_status=OperationStatus.SUCCEEDED,
                stage=OperationStage.CLEANING,
                reason=SafeResultCode.SUCCEEDED,
            )
            operation.status = OperationStatus.SUCCEEDED.value
            operation.stage_started_at = None
            operation.result_code = SafeResultCode.SUCCEEDED.value
            operation.completed_at = self._clock()
            operation.save(
                using=self.using,
                update_fields=("status", "stage_started_at", "result_code", "completed_at", "updated_at"),
            )
            if state.active_operation_id == operation.operation_id:
                state.active_operation = None
            if state.blocking_operation_id in {
                operation.operation_id,
                operation.subject_operation_id,
            }:
                state.blocking_operation = None
            state.save(using=self.using, update_fields=("active_operation", "blocking_operation", "updated_at"))
            return _operation_view(operation)

    def accept_recovery(
        self, command: AcceptedOperation
    ) -> RecoveryAccepted | OperationReplay | OperationConflict:
        if not isinstance(command, AcceptedOperation) or command.kind not in {
            OperationKind.RECHECK,
            OperationKind.CLEANUP,
        }:
            raise TypeError("invalid recovery command")
        with transaction.atomic(using=self.using):
            fence = self._reference_fence.lock_existing(command.channel_public_id)
            if fence.status != "locked":
                return OperationConflict(
                    "channel_unavailable" if fence.status == "channel_not_found" else fence.status
                )
            existing = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .filter(operation_id=command.operation_id)
                .first()
            )
            if existing is not None:
                if existing.request_fingerprint != command.request_fingerprint:
                    return OperationConflict("operation_conflict")
                return OperationReplay(_operation_view(existing))
            state = (
                RichMenuChannelState.objects.using(self.using)
                .select_for_update()
                .filter(channel_public_id=command.channel_public_id)
                .first()
            )
            if state is None or state.blocking_operation_id != command.subject_operation_id:
                return OperationConflict("invalid_relation")
            if state.active_operation_id is not None:
                return OperationConflict("operation_in_progress")
            subject = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .filter(
                    operation_id=command.subject_operation_id,
                    channel_state=state,
                    status__in=(OperationStatus.UNKNOWN.value, OperationStatus.CLEANUP_REQUIRED.value),
                )
                .first()
            )
            if subject is None or subject.operation_id == command.operation_id:
                return OperationConflict("invalid_relation")
            if (
                subject.owner_identity_public_id != command.owner_identity_public_id
                or subject.provider_id != command.provider_id
            ):
                return OperationConflict("invalid_relation")
            if command.kind is OperationKind.RECHECK:
                if subject.status != OperationStatus.UNKNOWN.value:
                    return OperationConflict("invalid_relation")
            elif (
                subject.status != OperationStatus.CLEANUP_REQUIRED.value
                or subject.kind == OperationKind.CLEANUP.value
            ):
                return OperationConflict("invalid_relation")
            target = None
            if command.kind is OperationKind.CLEANUP:
                target = (
                    ManagedRichMenu.objects.using(self.using)
                    .select_for_update()
                    .filter(
                        public_id=command.target_resource_id,
                        channel_state=state,
                        lifecycle__in=("candidate", "old", "cleanup_required"),
                    )
                    .first()
                )
                if target is None:
                    return OperationConflict("invalid_relation")
                directly_related = target.origin_operation_id == subject.operation_id
                replacement_related = target.replacement_operation_id == subject.operation_id
                if not directly_related and not replacement_related:
                    return OperationConflict("invalid_relation")
                if target.lifecycle == ResourceLifecycle.OLD.value and not replacement_related:
                    return OperationConflict("invalid_relation")
            recovery_fence = self._operation_fence.lock_exact(
                OperationFenceSnapshot(
                    owner_identity_public_id=command.owner_identity_public_id,
                    provider_id=command.provider_id,
                    channel_public_id=command.channel_public_id,
                    expected_channel_revision=command.expected_channel_revision,
                )
            )
            if recovery_fence.status != "matched":
                return OperationConflict(
                    "stale_channel"
                    if recovery_fence.status == "stale"
                    else "storage_unavailable"
                )
            now = self._clock()
            stage = (
                OperationStage.VERIFYING
                if command.kind is OperationKind.RECHECK
                else OperationStage.CLEANING
            )
            operation = RichMenuOperation.objects.using(self.using).create(
                operation_id=command.operation_id,
                channel_state=state,
                owner_identity_public_id=command.owner_identity_public_id,
                provider_id=command.provider_id,
                kind=command.kind.value,
                subject_operation=subject,
                target_resource=target,
                request_fingerprint=command.request_fingerprint,
                expected_channel_revision=command.expected_channel_revision,
                status=OperationStatus.RECOVERY_ACTIVE.value,
                stage=stage.value,
                stage_started_at=now,
                result_code=SafeResultCode.ACCEPTED.value,
                accepted_at=now,
            )
            self._append_transition(
                operation=operation,
                from_status=OperationStatus.ACCEPTED,
                to_status=OperationStatus.RECOVERY_ACTIVE,
                stage=stage,
                reason=SafeResultCode.ACCEPTED,
            )
            state.active_operation = operation
            state.save(using=self.using, update_fields=("active_operation", "updated_at"))
            return RecoveryAccepted(_operation_view(operation))

    def handoff_recovery(
        self, outcome: RecoveryOutcome
    ) -> RecoveryHandoffResult | StageConflict:
        if not isinstance(outcome, RecoveryOutcome):
            raise TypeError("invalid recovery outcome")
        with transaction.atomic(using=self.using):
            recovery = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .select_related("channel_state")
                .filter(operation_id=outcome.recovery_operation_id)
                .first()
            )
            if recovery is None:
                return StageConflict("operation_not_found")
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=recovery.channel_state_id
            )
            subject = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .filter(operation_id=outcome.subject_operation_id, channel_state=state)
                .first()
            )
            if (
                subject is None
                or recovery.subject_operation_id != outcome.subject_operation_id
                or state.active_operation_id != recovery.operation_id
                or state.blocking_operation_id != subject.operation_id
                or recovery.status != OperationStatus.RECOVERY_ACTIVE.value
            ):
                return StageConflict("stale_stage")

            recovery_fence = self._operation_fence.lock_exact(_fence_snapshot(recovery))
            if recovery_fence.status != "matched":
                return self._reject_stale_recovery(
                    recovery=recovery,
                    subject=subject,
                    state=state,
                    fence_status=recovery_fence.status,
                )

            now = self._clock()
            recovery_from = OperationStatus.RECOVERY_ACTIVE
            subject_from = OperationStatus(subject.status)
            if outcome.blocker_moves_to_recovery:
                if (
                    recovery.kind != OperationKind.CLEANUP.value
                    or subject_from is not OperationStatus.CLEANUP_REQUIRED
                    or subject.stage != OperationStage.CLEANING.value
                    or outcome.subject_next_status is not OperationStatus.CLEANUP_REQUIRED
                    or outcome.subject_next_stage is not OperationStage.CLEANING
                    or outcome.subject_result is not SafeResultCode.CLEANUP_REQUIRED
                ):
                    return StageConflict("invalid_transition")
                recovery.status = OperationStatus.UNKNOWN.value
                recovery.result_code = SafeResultCode.RESPONSE_UNKNOWN.value
                recovery.stage_started_at = None
                subject.status = outcome.subject_next_status.value
                subject.stage = outcome.subject_next_stage.value
                subject.result_code = outcome.subject_result.value
                state.blocking_operation = recovery
                state.active_operation = None
                recovery_to = OperationStatus.UNKNOWN
            else:
                if not _valid_recovery_subject_handoff(subject, outcome):
                    return StageConflict("invalid_transition")
                recovery.status = OperationStatus.SUCCEEDED.value
                recovery.result_code = SafeResultCode.SUCCEEDED.value
                recovery.stage_started_at = None
                recovery.completed_at = now
                subject.status = outcome.subject_next_status.value
                subject.stage = outcome.subject_next_stage.value
                subject.stage_started_at = None
                subject.result_code = outcome.subject_result.value
                if outcome.subject_next_status in {OperationStatus.SUCCEEDED, OperationStatus.FAILED}:
                    subject.completed_at = now
                    state.active_operation = None
                else:
                    state.active_operation = subject
                state.blocking_operation = None
                recovery_to = OperationStatus.SUCCEEDED
            self._append_transition(
                operation=recovery,
                from_status=recovery_from,
                to_status=recovery_to,
                stage=OperationStage(recovery.stage),
                reason=(
                    SafeResultCode.RESPONSE_UNKNOWN
                    if outcome.blocker_moves_to_recovery
                    else SafeResultCode.SUCCEEDED
                ),
            )
            self._append_transition(
                operation=subject,
                from_status=subject_from,
                to_status=outcome.subject_next_status,
                stage=outcome.subject_next_stage,
                reason=outcome.subject_result,
            )
            recovery.save(
                using=self.using,
                update_fields=("status", "result_code", "stage_started_at", "completed_at", "updated_at"),
            )
            subject.save(
                using=self.using,
                update_fields=("status", "stage", "stage_started_at", "result_code", "completed_at", "updated_at"),
            )
            state.save(
                using=self.using,
                update_fields=("blocking_operation", "active_operation", "updated_at"),
            )
            return RecoveryHandoffResult(
                recovery=_operation_view(recovery), subject=_operation_view(subject)
            )

    def _reject_stale_recovery(
        self,
        *,
        recovery: RichMenuOperation,
        subject: RichMenuOperation,
        state: RichMenuChannelState,
        fence_status: str,
    ) -> RecoveryHandoffResult:
        reason = (
            SafeResultCode.STALE_CHANNEL
            if fence_status == "stale"
            else SafeResultCode.STORAGE_UNAVAILABLE
        )
        self._append_transition(
            operation=recovery,
            from_status=OperationStatus.RECOVERY_ACTIVE,
            to_status=OperationStatus.FAILED,
            stage=OperationStage(recovery.stage),
            reason=reason,
        )
        recovery.status = OperationStatus.FAILED.value
        recovery.result_code = reason.value
        recovery.stage_started_at = None
        recovery.completed_at = self._clock()
        recovery.save(
            using=self.using,
            update_fields=(
                "status",
                "result_code",
                "stage_started_at",
                "completed_at",
                "updated_at",
            ),
        )
        state.active_operation = None
        state.save(using=self.using, update_fields=("active_operation", "updated_at"))
        return RecoveryHandoffResult(
            recovery=_operation_view(recovery), subject=_operation_view(subject)
        )

    def record_replacement(
        self,
        *,
        replacement_operation_id: UUID,
        new_resource_id: UUID,
        old_resource_id: UUID,
    ) -> ReplacementRecorded | OperationConflict:
        if not all(
            isinstance(value, UUID)
            for value in (replacement_operation_id, new_resource_id, old_resource_id)
        ):
            raise TypeError("invalid replacement relation")
        if new_resource_id == old_resource_id:
            return OperationConflict("invalid_relation")
        with transaction.atomic(using=self.using):
            operation = (
                RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .filter(operation_id=replacement_operation_id)
                .first()
            )
            if operation is None:
                return OperationConflict("invalid_relation")
            state = RichMenuChannelState.objects.using(self.using).select_for_update().get(
                channel_public_id=operation.channel_state_id
            )
            resources = {
                resource.public_id: resource
                for resource in ManagedRichMenu.objects.using(self.using)
                .select_for_update()
                .filter(
                    channel_state=state,
                    public_id__in=(new_resource_id, old_resource_id),
                )
            }
            new_resource = resources.get(new_resource_id)
            old_resource = resources.get(old_resource_id)
            if (
                operation.kind != OperationKind.APPLY.value
                or operation.status != OperationStatus.PROCESSING.value
                or operation.stage != OperationStage.VERIFYING.value
                or state.active_operation_id != operation.operation_id
                or state.current_resource_id != old_resource_id
                or new_resource is None
                or old_resource is None
                or new_resource.origin_operation_id != operation.operation_id
                or new_resource.lifecycle != ResourceLifecycle.CANDIDATE.value
                or old_resource.lifecycle != ResourceLifecycle.APPLIED.value
                or old_resource.replacement_operation_id is not None
            ):
                return OperationConflict("invalid_relation")
            replacement_fence = self._operation_fence.lock_exact(
                _fence_snapshot(operation)
            )
            if replacement_fence.status != "matched":
                return OperationConflict(
                    "stale_channel"
                    if replacement_fence.status == "stale"
                    else "storage_unavailable"
                )
            try:
                new_lifecycle = transition_resource(
                    ResourceLifecycle(new_resource.lifecycle), ResourceLifecycle.APPLIED
                )
                old_lifecycle = transition_resource(
                    ResourceLifecycle(old_resource.lifecycle), ResourceLifecycle.OLD
                )
            except InvalidStateTransition:
                return OperationConflict("invalid_relation")
            try:
                with transaction.atomic(using=self.using):
                    new_resource.lifecycle = new_lifecycle.value
                    new_resource.save(
                        using=self.using,
                        update_fields=("lifecycle", "updated_at"),
                    )
                    old_resource.lifecycle = old_lifecycle.value
                    old_resource.replacement_operation = operation
                    old_resource.save(
                        using=self.using,
                        update_fields=("lifecycle", "replacement_operation", "updated_at"),
                    )
                    state.current_resource = new_resource
                    state.save(
                        using=self.using,
                        update_fields=("current_resource", "updated_at"),
                    )
            except IntegrityError:
                return OperationConflict("invalid_relation")
            return ReplacementRecorded(
                current_resource=_resource_view(new_resource),
                old_resource=_resource_view(old_resource),
            )

    def get_state(self, scope: OwnerChannelScope) -> ChannelStateView | None:
        if not isinstance(scope, OwnerChannelScope):
            raise TypeError("invalid owner channel scope")
        state = (
            RichMenuChannelState.objects.using(self.using)
            .select_related(
                "current_resource",
                "blocking_operation",
                "active_operation",
            )
            .filter(channel_public_id=scope.channel_public_id)
            .first()
        )
        if state is None:
            return None
        owned_operations = RichMenuOperation.objects.using(self.using).filter(
            channel_state=state,
            owner_identity_public_id=scope.owner_identity_public_id,
            provider_id=scope.provider_id,
        )
        total_count = owned_operations.count()
        latest = owned_operations.order_by("-accepted_at", "-operation_id").first()
        if total_count == 0:
            return None
        cleanup_resources = tuple(
            _resource_view(resource)
            for resource in ManagedRichMenu.objects.using(self.using)
            .filter(
                channel_state=state,
                lifecycle__in=("old", "cleanup_required"),
            )
            .order_by("created_at", "public_id")
        )
        blocking = (
            state.blocking_operation
            if _operation_in_scope(state.blocking_operation, scope)
            else None
        )
        active = (
            state.active_operation
            if _operation_in_scope(state.active_operation, scope)
            else None
        )
        observation = _observation_view(state)
        next_actions = (
            (NextAllowedAction.GET_STATE, NextAllowedAction.VIEW_HISTORY)
            if active is not None
            else (NextAllowedAction.RECHECK, NextAllowedAction.GET_STATE, NextAllowedAction.VIEW_HISTORY)
            if blocking is not None and blocking.status == OperationStatus.UNKNOWN.value
            else (
                (NextAllowedAction.CLEANUP, NextAllowedAction.GET_STATE, NextAllowedAction.VIEW_HISTORY)
                if blocking is not None
                else (
                    (NextAllowedAction.GET_STATE, NextAllowedAction.VIEW_HISTORY)
                    if cleanup_resources
                    else (NextAllowedAction.APPLY, NextAllowedAction.GET_STATE, NextAllowedAction.VIEW_HISTORY)
                )
            )
        )
        return ChannelStateView(
            channel_public_id=state.channel_public_id,
            current_resource=(
                None if state.current_resource is None else _resource_view(state.current_resource)
            ),
            blocking_operation=None if blocking is None else _operation_view(blocking),
            active_operation=None if active is None else _operation_view(active),
            cleanup_resources=cleanup_resources,
            latest_observation=observation,
            history_summary=HistorySummary(
                total_count=total_count,
                latest_operation_id=latest.operation_id,
                latest_status=OperationStatus(latest.status),
            ),
            next_allowed_actions=next_actions,
        )

    def list_history(self, query: HistoryQuery) -> HistoryPage:
        if not isinstance(query, HistoryQuery):
            raise TypeError("invalid history query")
        operations = (
            RichMenuOperation.objects.using(self.using)
            .filter(
                channel_state_id=query.scope.channel_public_id,
                owner_identity_public_id=query.scope.owner_identity_public_id,
                provider_id=query.scope.provider_id,
            )
            .select_related("target_resource")
            .prefetch_related(
                Prefetch(
                    "transitions",
                    queryset=RichMenuOperationTransition.objects.using(self.using).order_by("sequence"),
                )
            )
            .order_by("-accepted_at", "-operation_id")
        )
        if query.cursor is not None:
            accepted_at, operation_id = _decode_cursor(query.cursor)
            operations = operations.filter(
                Q(accepted_at__lt=accepted_at)
                | Q(accepted_at=accepted_at, operation_id__lt=operation_id)
            )
        rows = list(operations[: query.limit + 1])
        has_more = len(rows) > query.limit
        page_rows = rows[: query.limit]
        entries = tuple(_history_entry(operation) for operation in page_rows)
        next_cursor = (
            _encode_cursor(page_rows[-1]) if has_more and page_rows else None
        )
        return HistoryPage(entries=entries, next_cursor=next_cursor, has_more=has_more)


def _operation_view(operation: RichMenuOperation) -> OperationView:
    return OperationView(
        operation_id=operation.operation_id,
        kind=OperationKind(operation.kind),
        status=OperationStatus(operation.status),
        stage=None if operation.stage is None else OperationStage(operation.stage),
        result=SafeResultCode(operation.result_code),
        subject_operation_id=operation.subject_operation_id,
        target_resource_id=operation.target_resource_id,
        accepted_at=operation.accepted_at,
        completed_at=operation.completed_at,
        next_allowed_actions=_next_actions(OperationStatus(operation.status)),
    )


def _resource_view(resource: ManagedRichMenu) -> ManagedResourceView:
    return ManagedResourceView(
        public_id=resource.public_id,
        origin_operation_id=resource.origin_operation_id,
        lifecycle=ResourceLifecycle(resource.lifecycle),
        image_digest=resource.image_digest,
    )


def _resource_target(resource: ManagedRichMenu) -> ManagedResourceTarget:
    return ManagedResourceTarget(
        public_id=resource.public_id,
        line_rich_menu_id=resource.line_rich_menu_id,
        lifecycle=ResourceLifecycle(resource.lifecycle),
        ownership_marker=resource.ownership_marker,
        origin_operation_id=resource.origin_operation_id,
        replacement_operation_id=resource.replacement_operation_id,
    )


def _operation_in_scope(
    operation: RichMenuOperation | None, scope: OwnerChannelScope
) -> bool:
    return operation is not None and (
        operation.owner_identity_public_id == scope.owner_identity_public_id
        and operation.provider_id == scope.provider_id
        and operation.channel_state_id == scope.channel_public_id
    )


def _observation_view(state: RichMenuChannelState) -> DefaultObservation | None:
    if state.last_observation_kind is None:
        return None
    kind = ObservationKind(state.last_observation_kind)
    managed_resource_id = None
    if kind in {ObservationKind.MANAGED_DEFAULT, ObservationKind.OTHER_MANAGED_DEFAULT}:
        if state.current_resource_id is None:
            kind = ObservationKind.UNKNOWN
        else:
            managed_resource_id = state.current_resource_id
    return DefaultObservation(
        kind=kind,
        observed_at=state.last_observed_at,
        fingerprint=state.last_observation_fingerprint,
        managed_resource_id=managed_resource_id,
    )


def _history_entry(operation: RichMenuOperation) -> HistoryEntry:
    configuration, channel_label = _configuration_from_snapshot(
        operation.configuration_snapshot
    )
    transitions = tuple(
        SafeResultCode(transition.safe_reason) for transition in operation.transitions.all()
    )
    status = OperationStatus(operation.status)
    if status is OperationStatus.UNKNOWN:
        default_relation = DefaultRelation.UNKNOWN
    elif operation.kind == OperationKind.APPLY.value and status is OperationStatus.SUCCEEDED:
        default_relation = DefaultRelation.BECAME_DEFAULT
    elif operation.kind == OperationKind.UNLINK.value and status is OperationStatus.SUCCEEDED:
        default_relation = DefaultRelation.CLEARED_DEFAULT
    elif operation.kind == OperationKind.RELEASE.value:
        default_relation = DefaultRelation.NOT_DEFAULT
    else:
        default_relation = DefaultRelation.UNKNOWN
    target_lifecycle = (
        None if operation.target_resource is None else operation.target_resource.lifecycle
    )
    if target_lifecycle == ResourceLifecycle.DELETED.value:
        cleanup_relation = CleanupRelation.COMPLETED
    elif status is OperationStatus.CLEANUP_REQUIRED or target_lifecycle in {
        ResourceLifecycle.OLD.value,
        ResourceLifecycle.CLEANUP_REQUIRED.value,
    }:
        cleanup_relation = CleanupRelation.REQUIRED
    elif status is OperationStatus.UNKNOWN and operation.kind == OperationKind.CLEANUP.value:
        cleanup_relation = CleanupRelation.UNKNOWN
    else:
        cleanup_relation = CleanupRelation.NOT_REQUIRED
    return HistoryEntry(
        operation=_operation_view(operation),
        channel_public_id=operation.channel_state_id,
        channel_label=channel_label,
        configuration=configuration,
        transitions=transitions,
        default_relation=default_relation,
        cleanup_relation=cleanup_relation,
    )


def _configuration_from_snapshot(
    snapshot: object,
) -> tuple[NormalizedTemplate | None, str]:
    if not isinstance(snapshot, dict):
        return None, "チャネル"
    channel_label = snapshot.get("channelLabel")
    if not isinstance(channel_label, str) or not channel_label:
        channel_label = "チャネル"
    try:
        template_id = snapshot["templateId"]
        template_version = snapshot["templateVersion"]
        raw_fields = snapshot["fields"]
        if not isinstance(raw_fields, list) or not raw_fields:
            return None, channel_label
        fields = tuple(
            TemplateFieldValue(
                display_name=field["displayName"], uri=field["uri"]
            )
            for field in raw_fields
            if isinstance(field, dict)
        )
        if len(fields) != len(raw_fields):
            return None, channel_label
        return (
            NormalizedTemplate(
                reference=TemplateReference(template_id=template_id, version=template_version),
                fields=fields,
            ),
            channel_label,
        )
    except (KeyError, TypeError, ValueError):
        return None, channel_label


_HISTORY_CURSOR_SALT = "linerichmenus.history.v1"


def _encode_cursor(operation: RichMenuOperation) -> str:
    return signing.dumps(
        {"acceptedAt": operation.accepted_at.isoformat(), "operationId": str(operation.operation_id)},
        salt=_HISTORY_CURSOR_SALT,
        compress=True,
    )


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        payload = signing.loads(cursor, salt=_HISTORY_CURSOR_SALT)
        accepted_at = datetime.fromisoformat(payload["acceptedAt"])
        operation_id = UUID(payload["operationId"])
        if timezone.is_naive(accepted_at):
            raise ValueError("naive cursor time")
        return accepted_at, operation_id
    except (signing.BadSignature, KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid history cursor") from error


def _fence_snapshot(operation: RichMenuOperation) -> OperationFenceSnapshot:
    return OperationFenceSnapshot(
        owner_identity_public_id=operation.owner_identity_public_id,
        provider_id=operation.provider_id,
        channel_public_id=operation.channel_state_id,
        expected_channel_revision=operation.expected_channel_revision,
    )


def _valid_recovery_subject_handoff(
    subject: RichMenuOperation, outcome: RecoveryOutcome
) -> bool:
    current_status = OperationStatus(subject.status)
    if current_status not in {OperationStatus.UNKNOWN, OperationStatus.CLEANUP_REQUIRED}:
        return False
    current_stage = OperationStage(subject.stage)
    if outcome.subject_next_status in {OperationStatus.SUCCEEDED, OperationStatus.FAILED}:
        return outcome.subject_next_stage is current_stage
    if outcome.subject_next_status is not OperationStatus.PROCESSING:
        return False
    successors = {
        OperationStage.CREATING: OperationStage.UPLOADING,
        OperationStage.UPLOADING: OperationStage.SETTING_DEFAULT,
        OperationStage.SETTING_DEFAULT: OperationStage.VERIFYING,
        OperationStage.CLEARING_DEFAULT: OperationStage.VERIFYING,
        OperationStage.CLEANING: OperationStage.CLEANING,
        OperationStage.VERIFYING: OperationStage.VERIFYING,
    }
    return successors.get(current_stage) is outcome.subject_next_stage


def _next_actions(status: OperationStatus) -> tuple[NextAllowedAction, ...]:
    if status is OperationStatus.UNKNOWN:
        return (NextAllowedAction.RECHECK, NextAllowedAction.GET_STATE)
    if status is OperationStatus.CLEANUP_REQUIRED:
        return (NextAllowedAction.CLEANUP, NextAllowedAction.GET_STATE)
    if status in {OperationStatus.SUCCEEDED, OperationStatus.FAILED}:
        return (NextAllowedAction.GET_STATE, NextAllowedAction.VIEW_HISTORY)
    return (NextAllowedAction.GET_STATE,)


def _require_digest(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("invalid digest")
