from django.db import DatabaseError, OperationalError, transaction
from django.utils import timezone

from .admin_lifecycle_types import (
    AdvanceDeactivationRevision,
    CompletedDeactivation,
    CompleteDeactivation,
    DeactivationConflict,
    DeactivationView,
    LockedDeactivation,
    RecordDeactivationRevisionConflict,
    ReservedDeactivation,
    ReserveDeactivation,
    SavedDeactivation,
    SaveDeactivationResult,
)
from .models import ChannelDeactivationState, LineChannel


class DjangoChannelDeactivationRepository:
    """current disable intentとchannel revisionを同じlock順序で扱う。"""

    def __init__(self, *, using: str = "default") -> None:
        self.using = using

    def get_for_owner(self, owner_identity_public_id, provider_id, channel_public_id):
        try:
            state = (
                ChannelDeactivationState.objects.using(self.using)
                .select_related("line_channel")
                .filter(
                    line_channel__public_id=channel_public_id,
                    line_channel__provider_id=provider_id,
                    owner_identity_public_id=owner_identity_public_id,
                    provider_id=provider_id,
                )
                .first()
            )
            if state is not None:
                return self._view(state)
            exists = (
                LineChannel.objects.using(self.using)
                .filter(
                    public_id=channel_public_id,
                    provider_id=provider_id,
                )
                .exists()
            )
            return None if exists else DeactivationConflict("channel_not_found")
        except DatabaseError:
            return DeactivationConflict("storage_unavailable")

    def reserve(self, command: ReserveDeactivation):
        try:
            with transaction.atomic(using=self.using):
                channel = self._lock_channel(command.channel_public_id, command.provider_id)
                if channel is None:
                    return DeactivationConflict("channel_not_found")
                if not channel.is_active:
                    return DeactivationConflict("channel_inactive")
                state = self._lock_state(channel)
                if state is not None:
                    same = (
                        state.owner_identity_public_id == command.owner_identity_public_id
                        and state.provider_id == command.provider_id
                        and state.operation_id == command.operation_id
                        and state.expected_channel_revision == command.expected_channel_revision
                    )
                    if same:
                        return ReservedDeactivation(self._view(state), replayed=True)
                    if state.status != "completed":
                        return DeactivationConflict("deactivation_conflict")
                    if channel.updated_at != command.expected_channel_revision:
                        return DeactivationConflict("stale_channel")
                    now = timezone.now()
                    state.operation_id = command.operation_id
                    state.owner_identity_public_id = command.owner_identity_public_id
                    state.provider_id = command.provider_id
                    state.expected_channel_revision = command.expected_channel_revision
                    state.status = ChannelDeactivationState.Status.CHECKING
                    state.safe_reason = None
                    state.subject_rich_operation_id = None
                    state.latest_recovery_operation_id = None
                    state.recovery_result_ready = False
                    state.accepted_at = now
                    state.updated_at = now
                    state.completed_at = None
                    state.full_clean(validate_unique=False)
                    state.save(
                        update_fields=(
                            "operation_id",
                            "owner_identity_public_id",
                            "provider_id",
                            "expected_channel_revision",
                            "status",
                            "safe_reason",
                            "subject_rich_operation_id",
                            "latest_recovery_operation_id",
                            "recovery_result_ready",
                            "accepted_at",
                            "updated_at",
                            "completed_at",
                        )
                    )
                    return ReservedDeactivation(self._view(state))
                if channel.updated_at != command.expected_channel_revision:
                    return DeactivationConflict("stale_channel")
                state = ChannelDeactivationState.objects.using(self.using).create(
                    line_channel=channel,
                    operation_id=command.operation_id,
                    owner_identity_public_id=command.owner_identity_public_id,
                    provider_id=command.provider_id,
                    expected_channel_revision=command.expected_channel_revision,
                    status=ChannelDeactivationState.Status.CHECKING,
                )
                return ReservedDeactivation(self._view(state))
        except OperationalError, DatabaseError:
            return DeactivationConflict("storage_unavailable")

    def save_result(self, command: SaveDeactivationResult):
        try:
            with transaction.atomic(using=self.using):
                locked = self._lock_current(command)
                if isinstance(locked, DeactivationConflict):
                    return locked
                channel, state = locked
                if channel.updated_at != command.expected_channel_revision:
                    return DeactivationConflict("stale_channel")
                if command.status == "unlinking":
                    if command.safe_reason is not None or command.subject_rich_operation_id is None:
                        return DeactivationConflict("invalid_result")
                elif not command.safe_reason:
                    return DeactivationConflict("invalid_result")
                state.status = command.status
                state.safe_reason = command.safe_reason
                state.subject_rich_operation_id = command.subject_rich_operation_id
                if command.recovery_operation_id is not None:
                    if state.latest_recovery_operation_id not in {
                        None,
                        command.recovery_operation_id,
                    }:
                        return DeactivationConflict("recovery_conflict")
                    state.latest_recovery_operation_id = command.recovery_operation_id
                    state.recovery_result_ready = True
                state.updated_at = timezone.now()
                state.full_clean(validate_unique=False)
                state.save(
                    update_fields=(
                        "status",
                        "safe_reason",
                        "subject_rich_operation_id",
                        "latest_recovery_operation_id",
                        "updated_at",
                        "recovery_result_ready",
                    )
                )
                return SavedDeactivation(self._view(state))
        except OperationalError, DatabaseError:
            return DeactivationConflict("storage_unavailable")

    def record_revision_conflict(self, command: RecordDeactivationRevisionConflict):
        try:
            with transaction.atomic(using=self.using):
                locked = self._lock_current(command, require_revision=False)
                if isinstance(locked, DeactivationConflict):
                    return locked
                _, state = locked
                state.status = ChannelDeactivationState.Status.CONFIRMATION_REQUIRED
                state.safe_reason = "stale_channel"
                if state.latest_recovery_operation_id is not None:
                    state.recovery_result_ready = True
                state.updated_at = timezone.now()
                state.full_clean(validate_unique=False)
                state.save(
                    update_fields=("status", "safe_reason", "recovery_result_ready", "updated_at")
                )
                return SavedDeactivation(self._view(state))
        except OperationalError, DatabaseError:
            return DeactivationConflict("storage_unavailable")

    def advance_recheck_revision(self, command: AdvanceDeactivationRevision):
        try:
            with transaction.atomic(using=self.using):
                locked = self._lock_current(command, require_revision=False)
                if isinstance(locked, DeactivationConflict):
                    return locked
                channel, state = locked
                if channel.updated_at != command.presented_channel_revision:
                    return DeactivationConflict("stale_channel")
                if state.latest_recovery_operation_id is not None:
                    if state.latest_recovery_operation_id != command.recovery_operation_id:
                        return DeactivationConflict("recovery_conflict")
                    if state.recovery_result_ready:
                        return LockedDeactivation(self._view(state))
                    if state.expected_channel_revision != command.presented_channel_revision:
                        state.expected_channel_revision = command.presented_channel_revision
                        state.updated_at = timezone.now()
                        state.save(update_fields=("expected_channel_revision", "updated_at"))
                    return LockedDeactivation(self._view(state))
                state.expected_channel_revision = command.presented_channel_revision
                state.latest_recovery_operation_id = command.recovery_operation_id
                state.recovery_result_ready = False
                state.updated_at = timezone.now()
                state.save(
                    update_fields=(
                        "expected_channel_revision",
                        "latest_recovery_operation_id",
                        "recovery_result_ready",
                        "updated_at",
                    )
                )
                return LockedDeactivation(self._view(state))
        except OperationalError, DatabaseError:
            return DeactivationConflict("storage_unavailable")

    def complete_inactive(self, command: CompleteDeactivation):
        try:
            with transaction.atomic(using=self.using):
                locked = self._lock_current(command)
                if isinstance(locked, DeactivationConflict):
                    return locked
                channel, state = locked
                if channel.updated_at != command.expected_channel_revision:
                    return DeactivationConflict("stale_channel")
                now = timezone.now()
                channel.is_active = False
                channel.updated_at = now
                channel.save(update_fields=("is_active", "updated_at"))
                state.status = ChannelDeactivationState.Status.COMPLETED
                state.safe_reason = None
                state.subject_rich_operation_id = None
                state.latest_recovery_operation_id = None
                state.recovery_result_ready = False
                state.completed_at = now
                state.updated_at = now
                state.full_clean(validate_unique=False)
                state.save(
                    update_fields=(
                        "status",
                        "safe_reason",
                        "subject_rich_operation_id",
                        "latest_recovery_operation_id",
                        "completed_at",
                        "updated_at",
                        "recovery_result_ready",
                    )
                )
                return CompletedDeactivation(self._view(state), channel.updated_at)
        except OperationalError, DatabaseError:
            return DeactivationConflict("storage_unavailable")

    def _lock_current(self, command, *, require_revision=True):
        channel = self._lock_channel(command.channel_public_id, command.provider_id)
        if channel is None:
            return DeactivationConflict("channel_not_found")
        if not channel.is_active:
            return DeactivationConflict("channel_inactive")
        state = self._lock_state(channel)
        if state is None:
            return DeactivationConflict("deactivation_not_found")
        if (
            state.owner_identity_public_id != command.owner_identity_public_id
            or state.provider_id != command.provider_id
            or state.operation_id != command.operation_id
        ):
            return DeactivationConflict("deactivation_conflict")
        if (
            require_revision
            and state.expected_channel_revision != command.expected_channel_revision
        ):
            return DeactivationConflict("stale_channel")
        return channel, state

    def _lock_channel(self, channel_public_id, provider_id):
        return (
            LineChannel.objects.using(self.using)
            .select_for_update()
            .filter(public_id=channel_public_id, provider_id=provider_id)
            .first()
        )

    def _lock_state(self, channel):
        return (
            ChannelDeactivationState.objects.using(self.using)
            .select_for_update()
            .filter(line_channel=channel)
            .first()
        )

    @staticmethod
    def _view(state):
        channel = state.line_channel
        return DeactivationView(
            channel_public_id=channel.public_id,
            channel_active=channel.is_active,
            channel_revision=channel.updated_at,
            operation_id=state.operation_id,
            expected_channel_revision=state.expected_channel_revision,
            status=state.status,
            safe_reason=state.safe_reason,
            subject_rich_operation_id=state.subject_rich_operation_id,
            latest_recovery_operation_id=state.latest_recovery_operation_id,
            accepted_at=state.accepted_at,
            updated_at=state.updated_at,
            completed_at=state.completed_at,
            recovery_result_ready=state.recovery_result_ready,
        )


class DjangoPendingDeactivationFence:
    """Rich-menu operation受付transaction内でpending disableをfenceする。"""

    def __init__(self, *, using: str = "default") -> None:
        self.using = using

    def allows(self, command) -> bool:
        if not transaction.get_connection(self.using).in_atomic_block:
            return False
        state = (
            ChannelDeactivationState.objects.using(self.using)
            .select_for_update()
            .filter(line_channel__public_id=command.channel_public_id)
            .first()
        )
        if state is None or state.status == "completed":
            return True
        same_owner = (
            state.owner_identity_public_id == command.owner_identity_public_id
            and state.provider_id == command.provider_id
        )
        if (
            same_owner
            and state.safe_reason == "cleanup_required"
            and getattr(getattr(command, "kind", None), "value", None) == "cleanup"
        ):
            # RichMenuRepository.accept_recovery が current blocker、target、
            # ownership relationを同じtransaction内で続けて検証する。
            return True
        return (
            same_owner
            and state.latest_recovery_operation_id == command.operation_id
            and state.subject_rich_operation_id == command.subject_operation_id
        )
