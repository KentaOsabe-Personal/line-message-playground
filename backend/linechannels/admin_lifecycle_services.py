from datetime import datetime

from django.db import transaction
from django.utils import timezone

from linerichmenus.headless import (
    DisableAssessment,
    HeadlessStateCommand,
    HeadlessUnlinkCommand,
    ReassessDisableState,
    ReconcileDisableSubject,
)

from .admin_lifecycle_types import (
    AdvanceDeactivationRevision,
    CompletedDeactivation,
    CompleteDeactivation,
    DeactivationConflict,
    DeactivationFailed,
    DeactivationSucceeded,
    ReactivateChannel,
    ReactivationSucceeded,
    RecheckDeactivation,
    RecordDeactivationRevisionConflict,
    ReservedDeactivation,
    ReserveDeactivation,
    SavedDeactivation,
    SaveDeactivationResult,
    StartDeactivation,
)
from .types import UpdateLineChannel


class DefaultChannelDeactivationCoordinator:
    def __init__(
        self,
        owner_fence,
        repository,
        rich_menu_lifecycle,
        channel_service,
        *,
        using: str = "default",
        clock=timezone.now,
    ) -> None:
        self._owner_fence = owner_fence
        self._repository = repository
        self._rich_menu_lifecycle = rich_menu_lifecycle
        self._channel_service = channel_service
        self._using = using
        self._clock = clock

    def get(self, owner, channel_id):
        proof = self._owner(owner)
        if isinstance(proof, DeactivationFailed):
            return proof
        result = self._repository.get_for_owner(
            proof.identity_public_id, proof.provider_id, channel_id
        )
        if isinstance(result, DeactivationConflict):
            return DeactivationFailed(result.code)
        return DeactivationSucceeded(result)

    def start(self, owner, command: StartDeactivation):
        proof = self._owner(owner)
        if isinstance(proof, DeactivationFailed):
            return proof
        reserved = self._repository.reserve(
            ReserveDeactivation(
                proof.identity_public_id,
                proof.provider_id,
                command.channel_public_id,
                command.operation_id,
                command.expected_channel_revision,
            )
        )
        if isinstance(reserved, DeactivationConflict):
            return DeactivationFailed(reserved.code)
        if not isinstance(reserved, ReservedDeactivation):
            return DeactivationFailed("storage_unavailable")
        if reserved.replayed:
            return DeactivationSucceeded(reserved.view)

        assessment = self._rich_menu_lifecycle.assess_disable(
            self._state_command(owner, proof.provider_id, reserved.view)
        )
        return self._apply_assessment(owner, proof, reserved.view, assessment)

    def recheck(self, owner, command: RecheckDeactivation):
        proof = self._owner(owner)
        if isinstance(proof, DeactivationFailed):
            return proof
        current = self._repository.get_for_owner(
            proof.identity_public_id, proof.provider_id, command.channel_public_id
        )
        if isinstance(current, DeactivationConflict):
            return DeactivationFailed(current.code)
        if current is None or current.operation_id != command.operation_id:
            return DeactivationFailed("deactivation_not_found")
        if current.status == "completed":
            return DeactivationSucceeded(current)
        if (
            current.latest_recovery_operation_id == command.recovery_operation_id
            and current.recovery_result_ready
        ):
            return DeactivationSucceeded(current)

        advanced = self._repository.advance_recheck_revision(
            AdvanceDeactivationRevision(
                proof.identity_public_id,
                proof.provider_id,
                command.channel_public_id,
                command.operation_id,
                command.recovery_operation_id,
                command.expected_channel_revision,
            )
        )
        if isinstance(advanced, DeactivationConflict):
            return DeactivationFailed(advanced.code)
        view = advanced.view
        base = dict(
            owner=owner,
            provider_id=proof.provider_id,
            channel_public_id=view.channel_public_id,
            expected_channel_revision=view.expected_channel_revision,
            deactivation_operation_id=view.operation_id,
            recovery_operation_id=command.recovery_operation_id,
        )
        if view.subject_rich_operation_id is not None:
            recovery = ReconcileDisableSubject(
                **base, subject_operation_id=view.subject_rich_operation_id
            )
        else:
            reason = {
                "cleanup_required": "cleanup_resolved",
                "stale_channel": "revision_changed",
            }.get(view.safe_reason, "external_default")
            recovery = ReassessDisableState(**base, reason=reason)
        recovered = self._rich_menu_lifecycle.recover_disable(recovery)
        return self._apply_assessment(
            owner,
            proof,
            view,
            recovered.assessment,
            recovery_operation_id=command.recovery_operation_id,
            allow_unlink=False,
        )

    def reactivate(self, owner, command: ReactivateChannel):
        proof = self._owner(owner)
        if isinstance(proof, DeactivationFailed):
            return proof
        result = self._channel_service.update(
            UpdateLineChannel(
                channel_public_id=command.channel_public_id,
                credentials=command.repair_credentials,
                is_active=True,
                expected_updated_at=command.expected_channel_revision,
                required_provider_id=proof.provider_id,
            )
        )
        if getattr(result, "status", None) != "succeeded":
            return DeactivationFailed(getattr(result, "code", "storage_unavailable"))
        return ReactivationSucceeded(
            result.channel.public_id, result.channel.updated_at, proof.provider_id
        )

    def _apply_assessment(
        self,
        owner,
        proof,
        view,
        assessment: DisableAssessment,
        *,
        recovery_operation_id=None,
        allow_unlink=True,
    ):
        if not isinstance(assessment, DisableAssessment):
            return self._save_confirmation(
                proof, view, "storage_unavailable", recovery_operation_id
            )
        if assessment.status == "clear_to_disable":
            completed = self._repository.complete_inactive(
                CompleteDeactivation(
                    proof.identity_public_id,
                    proof.provider_id,
                    view.channel_public_id,
                    view.operation_id,
                    view.expected_channel_revision,
                )
            )
            if isinstance(completed, CompletedDeactivation):
                return DeactivationSucceeded(completed.view)
            if isinstance(completed, DeactivationConflict) and completed.code == "stale_channel":
                return self._record_stale(proof, view)
            return DeactivationFailed(getattr(completed, "code", "storage_unavailable"))

        if assessment.status == "unlink_required" and allow_unlink:
            result = self._rich_menu_lifecycle.start_disable_unlink(
                HeadlessUnlinkCommand(
                    owner=owner,
                    provider_id=proof.provider_id,
                    channel_public_id=view.channel_public_id,
                    expected_channel_revision=view.expected_channel_revision,
                    deactivation_operation_id=view.operation_id,
                    assessment_proof=assessment.assessment_proof,
                )
            )
            operation = getattr(result, "operation", None)
            status = getattr(operation, "status", None)
            if getattr(status, "value", status) == "succeeded":
                reassessment = self._rich_menu_lifecycle.assess_disable(
                    self._state_command(owner, proof.provider_id, view)
                )
                return self._apply_assessment(
                    owner,
                    proof,
                    view,
                    reassessment,
                    recovery_operation_id=recovery_operation_id,
                    allow_unlink=False,
                )
            if getattr(status, "value", status) in {"accepted", "processing"}:
                return self._save(
                    proof,
                    view,
                    status="unlinking",
                    subject=view.operation_id,
                    recovery_operation_id=recovery_operation_id,
                )
            code = getattr(getattr(result, "code", None), "value", None)
            reason = {
                "rate_limited": "rate_limited",
                "authentication_failed": "authorization_failed",
                "stale_channel": "stale_channel",
            }.get(code, "recheck_required")
            return self._save_confirmation(
                proof,
                view,
                reason,
                recovery_operation_id,
                subject=(view.operation_id if operation is not None else None),
            )

        reason = (
            assessment.reason
            or {
                "unlink_required": "recheck_required",
                "external_default_blocked": "external_default",
                "recheck_required": "recheck_required",
                "cleanup_required": "cleanup_required",
                "unavailable": "storage_unavailable",
            }[assessment.status]
        )
        return self._save_confirmation(
            proof,
            view,
            reason,
            recovery_operation_id,
            subject=assessment.subject_operation_id,
        )

    def _save_confirmation(self, proof, view, reason, recovery_id, subject=None):
        return self._save(
            proof,
            view,
            status="confirmation_required",
            reason=reason,
            subject=subject,
            recovery_operation_id=recovery_id,
        )

    def _save(
        self,
        proof,
        view,
        *,
        status,
        reason=None,
        subject=None,
        recovery_operation_id=None,
    ):
        saved = self._repository.save_result(
            SaveDeactivationResult(
                proof.identity_public_id,
                proof.provider_id,
                view.channel_public_id,
                view.operation_id,
                view.expected_channel_revision,
                status,
                reason,
                subject,
                recovery_operation_id,
            )
        )
        if isinstance(saved, SavedDeactivation):
            return DeactivationSucceeded(saved.view)
        if isinstance(saved, DeactivationConflict) and saved.code == "stale_channel":
            return self._record_stale(proof, view)
        return DeactivationFailed(getattr(saved, "code", "storage_unavailable"))

    def _record_stale(self, proof, view):
        recorded = self._repository.record_revision_conflict(
            RecordDeactivationRevisionConflict(
                proof.identity_public_id,
                proof.provider_id,
                view.channel_public_id,
                view.operation_id,
            )
        )
        if isinstance(recorded, SavedDeactivation):
            return DeactivationSucceeded(recorded.view)
        return DeactivationFailed(getattr(recorded, "code", "storage_unavailable"))

    def _owner(self, owner):
        try:
            with transaction.atomic(using=self._using):
                result = self._owner_fence.lock_active(owner, self._aware_now())
        except Exception:
            return DeactivationFailed("storage_unavailable")
        if getattr(result, "status", None) == "failed":
            return DeactivationFailed(result.code)
        return result

    def _aware_now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or timezone.is_naive(now):
            raise TypeError("invalid clock")
        return now

    @staticmethod
    def _state_command(owner, provider_id, view):
        return HeadlessStateCommand(
            owner=owner,
            provider_id=provider_id,
            channel_public_id=view.channel_public_id,
            expected_channel_revision=view.expected_channel_revision,
        )
