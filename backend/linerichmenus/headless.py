from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from django.db import DatabaseError, transaction
from django.utils import timezone

from lineaccounts.admin_authorization import OwnerOperationContext
from linechannels.reference_fence import ChannelReferenceFence, DjangoChannelReferenceFence

from .models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation
from .repository import disable_assessment_proof
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
        if not isinstance(self.expected_channel_revision, datetime) or timezone.is_naive(
            self.expected_channel_revision
        ):
            raise ValueError("invalid channel revision")
        if self.operation is not None:
            if not isinstance(self.operation, OperationCommand):
                raise ValueError("invalid operation")
            if self.operation.channel_public_id != self.channel_public_id:
                raise ValueError("operation channel mismatch")
            if self.operation.expected_channel_revision != self.expected_channel_revision:
                raise ValueError("operation revision mismatch")


@dataclass(frozen=True, slots=True)
class HeadlessGuardResult:
    status: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"clear_to_disable", "blocked", "unavailable"}:
            raise ValueError("invalid guard result")


@dataclass(frozen=True, slots=True)
class HeadlessStateCommand:
    owner: OwnerOperationContext
    provider_id: str
    channel_public_id: UUID
    expected_channel_revision: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.owner, OwnerOperationContext):
            raise ValueError("invalid owner context")
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("invalid provider")
        if not isinstance(self.channel_public_id, UUID):
            raise ValueError("invalid channel")
        if not isinstance(self.expected_channel_revision, datetime) or timezone.is_naive(
            self.expected_channel_revision
        ):
            raise ValueError("invalid revision")


@dataclass(frozen=True, slots=True)
class HeadlessUnlinkCommand(HeadlessStateCommand):
    deactivation_operation_id: UUID
    assessment_proof: str

    def __post_init__(self) -> None:
        super(HeadlessUnlinkCommand, self).__post_init__()
        if not isinstance(self.deactivation_operation_id, UUID):
            raise ValueError("invalid deactivation operation")
        if not isinstance(self.assessment_proof, str) or len(self.assessment_proof) != 64:
            raise ValueError("invalid assessment proof")


@dataclass(frozen=True, slots=True)
class ReconcileDisableSubject(HeadlessStateCommand):
    deactivation_operation_id: UUID
    recovery_operation_id: UUID
    subject_operation_id: UUID

    def __post_init__(self) -> None:
        HeadlessStateCommand.__post_init__(self)
        if not all(
            isinstance(value, UUID)
            for value in (
                self.deactivation_operation_id,
                self.recovery_operation_id,
                self.subject_operation_id,
            )
        ):
            raise ValueError("invalid disable subject recovery")


@dataclass(frozen=True, slots=True)
class ReassessDisableState(HeadlessStateCommand):
    deactivation_operation_id: UUID
    recovery_operation_id: UUID
    reason: str

    def __post_init__(self) -> None:
        HeadlessStateCommand.__post_init__(self)
        if not all(
            isinstance(value, UUID)
            for value in (
                self.deactivation_operation_id,
                self.recovery_operation_id,
            )
        ):
            raise ValueError("invalid disable reassessment")
        if self.reason not in {"external_default", "cleanup_resolved", "revision_changed"}:
            raise ValueError("invalid disable reassessment reason")


HeadlessDisableRecoveryCommand = ReconcileDisableSubject | ReassessDisableState


@dataclass(frozen=True, slots=True)
class DisableAssessment:
    status: str
    assessment_proof: str | None = None
    target_resource_id: UUID | None = None
    subject_operation_id: UUID | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "clear_to_disable",
            "unlink_required",
            "external_default_blocked",
            "recheck_required",
            "cleanup_required",
            "unavailable",
        }:
            raise ValueError("invalid disable assessment")
        relation = (
            self.assessment_proof is not None,
            self.target_resource_id is not None,
            self.subject_operation_id is not None,
            self.reason is not None,
        )
        expected = {
            "clear_to_disable": (False, False, False, False),
            "unlink_required": (True, True, False, False),
            "external_default_blocked": (False, False, False, True),
            "recheck_required": (False, False, True, True),
            "cleanup_required": (False, True, False, True),
            "unavailable": (False, False, False, True),
        }[self.status]
        if relation != expected:
            raise ValueError("invalid disable assessment variant")
        if self.assessment_proof is not None and len(self.assessment_proof) != 64:
            raise ValueError("invalid assessment proof")


@dataclass(frozen=True, slots=True)
class DisableRecoveryResult:
    recovery_operation_id: UUID
    operation_result: OperationResult | None
    assessment: DisableAssessment

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_operation_id, UUID):
            raise ValueError("invalid recovery operation id")
        if not isinstance(self.assessment, DisableAssessment):
            raise ValueError("invalid recovery assessment")


@runtime_checkable
class RichMenuLifecyclePort(Protocol):
    def assess_disable(self, command: HeadlessStateCommand) -> DisableAssessment: ...

    def start_disable_unlink(self, command: HeadlessUnlinkCommand) -> OperationResult: ...

    def recover_disable(self, command: HeadlessDisableRecoveryCommand) -> DisableRecoveryResult: ...


@dataclass(frozen=True, slots=True)
class DisableRecoveryLookup:
    assessment: DisableAssessment | None = None
    conflict: bool = False


class DisableRecoveryStore(Protocol):
    """ChannelDeactivationRepository所有のcurrent projectionへのadapter。"""

    def lookup(self, command: HeadlessDisableRecoveryCommand) -> DisableRecoveryLookup: ...

    def save(
        self,
        command: HeadlessDisableRecoveryCommand,
        assessment: DisableAssessment,
    ) -> DisableRecoveryLookup: ...


class DefaultRichMenuLifecyclePort:
    def __init__(self, service, *, recovery_store=None) -> None:
        self._service = service
        self._recovery_store = recovery_store

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

    def assess_disable(self, command: HeadlessStateCommand) -> DisableAssessment:
        if not isinstance(command, HeadlessStateCommand):
            raise HeadlessContractProgrammingError("invalid_assessment_command")
        result = self._service.get_state(
            command.owner,
            command.channel_public_id,
            expected_channel_revision=command.expected_channel_revision,
        )
        if isinstance(result, ServiceFailed):
            return DisableAssessment("unavailable", reason=result.code.value)
        if not isinstance(result, StateSucceeded):
            return DisableAssessment("unavailable", reason="storage_unavailable")
        state = result.state
        if state.cleanup_resources:
            return DisableAssessment(
                "cleanup_required",
                target_resource_id=state.cleanup_resources[0].public_id,
                reason="cleanup_required",
            )
        subject = state.blocking_operation or state.active_operation
        if subject is not None:
            return DisableAssessment(
                "recheck_required",
                subject_operation_id=subject.operation_id,
                reason="recheck_required",
            )
        observation = state.latest_observation
        if observation is None or observation.kind is ObservationKind.UNKNOWN:
            return DisableAssessment("unavailable", reason="observation_unknown")
        if observation.kind is ObservationKind.DEFAULT_NONE and state.current_resource is None:
            return DisableAssessment("clear_to_disable")
        target = state.current_resource
        if (
            observation.kind is ObservationKind.MANAGED_DEFAULT
            and target is not None
            and observation.managed_resource_id == target.public_id
        ):
            proof_builder = getattr(type(self._service), "build_disable_assessment_proof", None)
            if callable(proof_builder):
                proof = proof_builder(
                    self._service,
                    command.owner,
                    provider_id=command.provider_id,
                    channel_public_id=command.channel_public_id,
                    expected_channel_revision=command.expected_channel_revision,
                    resource_id=target.public_id,
                    observation_fingerprint=observation.fingerprint,
                )
                if proof is None:
                    return DisableAssessment("unavailable", reason="ownership_unavailable")
            else:
                proof = disable_assessment_proof(
                    channel_public_id=command.channel_public_id,
                    expected_channel_revision=command.expected_channel_revision,
                    resource_id=target.public_id,
                    observation_fingerprint=observation.fingerprint,
                )
            return DisableAssessment(
                "unlink_required",
                assessment_proof=proof,
                target_resource_id=target.public_id,
            )
        return DisableAssessment("external_default_blocked", reason="external_default")

    def start_disable_unlink(self, command: HeadlessUnlinkCommand) -> OperationResult:
        if not isinstance(command, HeadlessUnlinkCommand):
            raise HeadlessContractProgrammingError("invalid_disable_unlink_command")
        starter = getattr(self._service, "start_disable_unlink", None)
        if not callable(starter):
            return ServiceFailed(self._storage_unavailable_code())
        return starter(command)

    def recover_disable(self, command: HeadlessDisableRecoveryCommand) -> DisableRecoveryResult:
        if not isinstance(command, (ReconcileDisableSubject, ReassessDisableState)):
            raise HeadlessContractProgrammingError("invalid_disable_recovery_command")
        if self._recovery_store is None:
            return DisableRecoveryResult(
                command.recovery_operation_id,
                None,
                DisableAssessment("unavailable", reason="integration_not_ready"),
            )
        try:
            replay = self._recovery_store.lookup(command)
        except Exception:
            return DisableRecoveryResult(
                command.recovery_operation_id,
                None,
                DisableAssessment("unavailable", reason="storage_unavailable"),
            )
        if replay.conflict:
            return DisableRecoveryResult(
                command.recovery_operation_id,
                None,
                DisableAssessment("unavailable", reason="operation_conflict"),
            )
        if replay.assessment is not None:
            operation_result = None
            if isinstance(command, ReconcileDisableSubject):
                operation_result = self._service.get_operation(
                    command.owner, command.recovery_operation_id
                )
            return DisableRecoveryResult(
                command.recovery_operation_id, operation_result, replay.assessment
            )
        operation_result = None
        if isinstance(command, ReconcileDisableSubject):
            operation_result = self._service.start_operation(
                command.owner,
                OperationCommand(
                    operation_id=command.recovery_operation_id,
                    channel_public_id=command.channel_public_id,
                    expected_channel_revision=command.expected_channel_revision,
                    kind=OperationKind.RECHECK,
                    subject_operation_id=command.subject_operation_id,
                    target_resource_id=None,
                ),
            )
            if isinstance(operation_result, ServiceFailed):
                failed = DisableRecoveryResult(
                    command.recovery_operation_id,
                    operation_result,
                    DisableAssessment("unavailable", reason=operation_result.code.value),
                )
                try:
                    self._recovery_store.save(command, failed.assessment)
                except Exception:
                    return DisableRecoveryResult(
                        command.recovery_operation_id,
                        operation_result,
                        DisableAssessment("unavailable", reason="storage_unavailable"),
                    )
                return failed
        assessment = self.assess_disable(
            HeadlessStateCommand(
                owner=command.owner,
                provider_id=command.provider_id,
                channel_public_id=command.channel_public_id,
                expected_channel_revision=command.expected_channel_revision,
            )
        )
        try:
            saved = self._recovery_store.save(command, assessment)
        except Exception:
            return DisableRecoveryResult(
                command.recovery_operation_id,
                operation_result,
                DisableAssessment("unavailable", reason="storage_unavailable"),
            )
        if saved.conflict or saved.assessment is None:
            assessment = DisableAssessment("unavailable", reason="operation_conflict")
        else:
            assessment = saved.assessment
        return DisableRecoveryResult(command.recovery_operation_id, operation_result, assessment)

    @staticmethod
    def _storage_unavailable_code():
        from .types import SafeResultCode

        return SafeResultCode.STORAGE_UNAVAILABLE

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
            state.filter(operations__status__in=self._BLOCKING_OPERATION_STATUSES).exists()
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
        if (
            RichMenuOperation.objects.using(self.using)
            .filter(
                channel_state=state,
                status__in=self._BLOCKING_OPERATION_STATUSES,
            )
            .exists()
        ):
            return True
        return (
            ManagedRichMenu.objects.using(self.using)
            .filter(
                channel_state=state,
                lifecycle__in=self._BLOCKING_RESOURCE_LIFECYCLES,
            )
            .exists()
        )

    def _delete_operations(self, state: RichMenuChannelState) -> None:
        operations = RichMenuOperation.objects.using(self.using).filter(channel_state=state)
        operations.filter(kind__in=("recheck", "cleanup")).delete()
        operations.delete()
