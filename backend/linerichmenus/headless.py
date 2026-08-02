from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import DatabaseError, transaction
from django.utils import timezone

from linechannels.reference_fence import ChannelReferenceFence, DjangoChannelReferenceFence
from lineaccounts.admin_authorization import OwnerOperationContext

from .models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation
from .services import OperationResult, ServiceFailed, StateSucceeded
from .types import ObservationKind, OperationCommand, OperationKind


class HeadlessContractProgrammingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HistoryPurgeResult:
    status: str

    def __post_init__(self) -> None:
        if self.status not in {
            "purged",
            "not_found",
            "blocked",
            "storage_unavailable",
        }:
            raise ValueError("invalid purge result")


@dataclass(frozen=True, slots=True)
class HeadlessCommand:
    owner: OwnerOperationContext
    channel_public_id: UUID
    expected_channel_revision: datetime
    operation: OperationCommand | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.owner, OwnerOperationContext):
            raise ValueError("invalid owner context")
        if not isinstance(self.channel_public_id, UUID):
            raise ValueError("invalid channel public id")
        if (
            not isinstance(self.expected_channel_revision, datetime)
            or timezone.is_naive(self.expected_channel_revision)
        ):
            raise ValueError("invalid channel revision")
        if self.operation is not None:
            if not isinstance(self.operation, OperationCommand):
                raise ValueError("invalid operation")
            if self.operation.channel_public_id != self.channel_public_id:
                raise ValueError("operation channel mismatch")
            if (
                self.operation.expected_channel_revision
                != self.expected_channel_revision
            ):
                raise ValueError("operation revision mismatch")


@dataclass(frozen=True, slots=True)
class HeadlessGuardResult:
    status: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"clear_to_disable", "blocked", "unavailable"}:
            raise ValueError("invalid guard result")


class DefaultRichMenuLifecyclePort:
    def __init__(self, service) -> None:
        self._service = service

    def get_guard_state(self, command: HeadlessCommand) -> HeadlessGuardResult:
        if not isinstance(command, HeadlessCommand) or command.operation is not None:
            raise HeadlessContractProgrammingError("invalid_guard_command")
        result = self._service.get_state(
            command.owner,
            command.channel_public_id,
            expected_channel_revision=command.expected_channel_revision,
        )
        if isinstance(result, ServiceFailed):
            return HeadlessGuardResult("unavailable", result.code.value)
        if not isinstance(result, StateSucceeded):
            return HeadlessGuardResult("unavailable", "storage_unavailable")
        state = result.state
        clear = (
            state.current_resource is None
            and state.blocking_operation is None
            and state.active_operation is None
            and not state.cleanup_resources
            and state.latest_observation is not None
            and state.latest_observation.kind is ObservationKind.DEFAULT_NONE
        )
        return HeadlessGuardResult(
            "clear_to_disable" if clear else "blocked",
            None if clear else "rich_menu_state_unresolved",
        )

    def start_unlink(self, command: HeadlessCommand) -> OperationResult:
        return self._start(command, OperationKind.UNLINK)

    def recheck(self, command: HeadlessCommand) -> OperationResult:
        return self._start(command, OperationKind.RECHECK)

    def _start(self, command: HeadlessCommand, kind: OperationKind) -> OperationResult:
        if (
            not isinstance(command, HeadlessCommand)
            or command.operation is None
            or command.operation.kind is not kind
        ):
            raise HeadlessContractProgrammingError("invalid_operation_command")
        return self._service.start_operation(command.owner, command.operation)


class DjangoHeadlessReferenceContracts:
    _BLOCKING_OPERATION_STATUSES = (
        "accepted",
        "processing",
        "unknown",
        "cleanup_required",
        "recovery_active",
    )
    _BLOCKING_RESOURCE_LIFECYCLES = (
        "candidate",
        "applied",
        "old",
        "cleanup_required",
    )

    def __init__(
        self,
        *,
        using: str = "default",
        reference_fence: ChannelReferenceFence | None = None,
    ) -> None:
        self.using = using
        self._reference_fence = reference_fence or DjangoChannelReferenceFence(using=using)

    def is_referenced(self, channel_public_id: UUID) -> bool:
        if not isinstance(channel_public_id, UUID):
            raise HeadlessContractProgrammingError("invalid_channel_public_id")
        state = RichMenuChannelState.objects.using(self.using).filter(
            channel_public_id=channel_public_id
        )
        if not state.exists():
            return False
        return (
            state.filter(
                operations__status__in=self._BLOCKING_OPERATION_STATUSES
            ).exists()
            or state.filter(
                managed_resources__lifecycle__in=self._BLOCKING_RESOURCE_LIFECYCLES
            ).exists()
            or state.filter(blocking_operation__isnull=False).exists()
            or state.filter(active_operation__isnull=False).exists()
        )

    def purge_history(self, channel_public_id: UUID) -> HistoryPurgeResult:
        connection = transaction.get_connection(self.using)
        if not connection.in_atomic_block:
            raise HeadlessContractProgrammingError("transaction_required")
        if not isinstance(channel_public_id, UUID):
            transaction.set_rollback(True, using=self.using)
            raise HeadlessContractProgrammingError("invalid_channel_public_id")
        try:
            fence = self._reference_fence.lock_existing(channel_public_id)
            if fence.status != "locked":
                transaction.set_rollback(True, using=self.using)
                return HistoryPurgeResult(
                    "not_found" if fence.status == "channel_not_found" else "storage_unavailable"
                )
            state = (
                RichMenuChannelState.objects.using(self.using)
                .select_for_update()
                .filter(channel_public_id=channel_public_id)
                .first()
            )
            if state is None:
                return HistoryPurgeResult("not_found")
            if self._locked_state_is_referenced(state):
                transaction.set_rollback(True, using=self.using)
                return HistoryPurgeResult("blocked")

            # Recovery children are removed before their nullable subject/target
            # relations could be cleared by deleting the parent rows.
            state.blocking_operation = None
            state.active_operation = None
            state.current_resource = None
            state.save(
                using=self.using,
                update_fields=(
                    "blocking_operation",
                    "active_operation",
                    "current_resource",
                    "updated_at",
                ),
            )
            ManagedRichMenu.objects.using(self.using).filter(
                channel_state=state,
                lifecycle__in=("deleted", "released"),
            ).delete()
            self._delete_operations(state)
            state.delete(using=self.using)
            return HistoryPurgeResult("purged")
        except DatabaseError:
            transaction.set_rollback(True, using=self.using)
            return HistoryPurgeResult("storage_unavailable")

    def _locked_state_is_referenced(self, state: RichMenuChannelState) -> bool:
        if state.blocking_operation_id is not None or state.active_operation_id is not None:
            return True
        if RichMenuOperation.objects.using(self.using).filter(
            channel_state=state,
            status__in=self._BLOCKING_OPERATION_STATUSES,
        ).exists():
            return True
        return ManagedRichMenu.objects.using(self.using).filter(
            channel_state=state,
            lifecycle__in=self._BLOCKING_RESOURCE_LIFECYCLES,
        ).exists()

    def _delete_operations(self, state: RichMenuChannelState) -> None:
        operations = RichMenuOperation.objects.using(self.using).filter(channel_state=state)
        operations.filter(kind__in=("recheck", "cleanup")).delete()
        operations.delete()
