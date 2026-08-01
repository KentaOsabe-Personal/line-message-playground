from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from lineaccounts.admin_authorization import (
    OwnerActiveProof,
    OwnerOperationContext,
)

from .admin_types import (
    AdminChannelMutationSucceeded,
    AdminConnectionSnapshot,
    AdminRepositoryFailed,
    AdminRepositoryUnavailable,
    AdminServiceFailed,
    BotIdentityReceived,
    ChannelDeleteSucceeded,
    ChannelListSucceeded,
    ChannelReadSucceeded,
    ConnectionCheckCompleted,
    DeleteAdminChannel,
    RegisterAdminChannel,
    SetAdminChannelState,
    SnapshotAvailable,
    UpdateAdminChannel,
)
from .reference_fence import ReferenceCheckResult
from .repositories import PersistenceError
from .types import RegisterLineChannel, UpdateLineChannel


class _OwnerFence(Protocol):
    def lock_active(self, context: OwnerOperationContext, now: datetime): ...


class _AdminRepository(Protocol):
    def list_for_owner_provider(self, owner_provider_id: str): ...

    def get_for_owner_provider(self, public_id: UUID, owner_provider_id: str): ...

    def get_connection_snapshot(self, public_id: UUID, owner_provider_id: str): ...

    def lock_connection_revision(
        self, public_id: UUID, owner_provider_id: str, expected_updated_at: datetime
    ): ...

    def lock_for_delete(self, public_id: UUID, owner_provider_id: str): ...

    def delete_locked(self, channel): ...


class _FoundationService(Protocol):
    def register(self, command: RegisterLineChannel): ...

    def update(self, command: UpdateLineChannel): ...


class _ReferenceDirectory(Protocol):
    def is_referenced(self, channel_public_id: UUID) -> ReferenceCheckResult: ...


class _BotInfoGateway(Protocol):
    def get_bot_identity(self, access_token): ...


class DefaultChannelAdminService:
    def __init__(
        self,
        owner_fence: _OwnerFence,
        repository: _AdminRepository,
        foundation_service: _FoundationService,
        reference_directory: _ReferenceDirectory,
        bot_info_gateway: _BotInfoGateway,
        *,
        using: str = "default",
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._owner_fence = owner_fence
        self._repository = repository
        self._foundation_service = foundation_service
        self._reference_directory = reference_directory
        self._bot_info_gateway = bot_info_gateway
        self._using = using
        self._clock = clock

    def list_channels(self, owner: OwnerOperationContext):
        try:
            with transaction.atomic(using=self._using):
                proof = self._lock_owner(owner)
                if isinstance(proof, AdminServiceFailed):
                    return proof
                channels = self._repository.list_for_owner_provider(proof.provider_id)
                return ChannelListSucceeded(channels)
        except PersistenceError as error:
            return AdminServiceFailed(self._storage_code(error))

    def get_channel(self, owner: OwnerOperationContext, channel_id: UUID):
        try:
            with transaction.atomic(using=self._using):
                proof = self._lock_owner(owner)
                if isinstance(proof, AdminServiceFailed):
                    return proof
                channel = self._repository.get_for_owner_provider(
                    channel_id, proof.provider_id
                )
                if channel is None:
                    return AdminServiceFailed("channel_not_found")
                return ChannelReadSucceeded(channel)
        except PersistenceError as error:
            return AdminServiceFailed(self._storage_code(error))

    def register(self, owner: OwnerOperationContext, command: RegisterAdminChannel):
        try:
            with transaction.atomic(using=self._using):
                proof = self._lock_owner(owner)
                if isinstance(proof, AdminServiceFailed):
                    return proof
                if command.provider_id != proof.provider_id:
                    return AdminServiceFailed("provider_mismatch")
                result = self._foundation_service.register(
                    RegisterLineChannel(
                        messaging_api_channel_id=command.messaging_api_channel_id,
                        bot_user_id=command.bot_user_id,
                        label=command.label,
                        credentials=command.credentials,
                        is_active=command.is_active,
                        provider_id=proof.provider_id,
                    )
                )
                if result.status == "failed":
                    return AdminServiceFailed(self._mutation_code(result.code))
                return self._project_mutation(result.channel.public_id, proof.provider_id)
        except (AttributeError, TypeError):
            return AdminServiceFailed("invalid_input")
        except PersistenceError as error:
            return AdminServiceFailed(self._storage_code(error))

    def update(self, owner: OwnerOperationContext, command: UpdateAdminChannel):
        try:
            with transaction.atomic(using=self._using):
                proof = self._lock_owner(owner)
                if isinstance(proof, AdminServiceFailed):
                    return proof
                if command.provider_id is not None and command.provider_id != proof.provider_id:
                    return AdminServiceFailed("provider_mismatch")
                return self._update_locked(proof, command, state_change=False)
        except (AttributeError, TypeError):
            return AdminServiceFailed("invalid_input")
        except PersistenceError as error:
            return AdminServiceFailed(self._storage_code(error))

    def set_state(self, owner: OwnerOperationContext, command: SetAdminChannelState):
        try:
            with transaction.atomic(using=self._using):
                proof = self._lock_owner(owner)
                if isinstance(proof, AdminServiceFailed):
                    return proof
                update = UpdateAdminChannel(
                    channel_public_id=command.channel_public_id,
                    expected_updated_at=command.expected_updated_at,
                    credentials=command.repair_credentials,
                )
                foundation_command = UpdateLineChannel(
                    channel_public_id=update.channel_public_id,
                    credentials=update.credentials,
                    is_active=command.is_active,
                    expected_updated_at=update.expected_updated_at,
                    required_provider_id=proof.provider_id,
                )
                result = self._foundation_service.update(foundation_command)
                if result.status == "failed":
                    return AdminServiceFailed(
                        self._mutation_code(result.code, state_change=True)
                    )
                return self._project_mutation(result.channel.public_id, proof.provider_id)
        except (AttributeError, TypeError):
            return AdminServiceFailed("invalid_input")
        except PersistenceError as error:
            return AdminServiceFailed(self._storage_code(error))

    def delete(self, owner: OwnerOperationContext, command: DeleteAdminChannel):
        try:
            with transaction.atomic(using=self._using):
                proof = self._lock_owner(owner)
                if isinstance(proof, AdminServiceFailed):
                    return proof
                locked = self._repository.lock_for_delete(
                    command.channel_public_id, proof.provider_id
                )
                if locked is None:
                    return AdminServiceFailed("channel_not_found")
                if locked.updated_at != command.expected_updated_at:
                    return AdminServiceFailed("stale_channel")
                reference = self._reference_directory.is_referenced(locked.public_id)
                if reference.status == "referenced":
                    return AdminServiceFailed("channel_referenced")
                if reference.status != "unreferenced":
                    return AdminServiceFailed(reference.status)
                public_id, label = self._repository.delete_locked(locked)
                return ChannelDeleteSucceeded(public_id, label)
        except (AttributeError, TypeError):
            return AdminServiceFailed("invalid_input")
        except PersistenceError as error:
            return AdminServiceFailed(self._storage_code(error))

    def check_connection(self, owner: OwnerOperationContext, channel_id: UUID):
        try:
            with transaction.atomic(using=self._using):
                initial_proof = self._lock_owner(owner)
                if isinstance(initial_proof, AdminServiceFailed):
                    return initial_proof
                snapshot_result = self._repository.get_connection_snapshot(
                    channel_id, initial_proof.provider_id
                )
        except PersistenceError as error:
            return AdminServiceFailed(self._storage_code(error))

        if isinstance(snapshot_result, AdminRepositoryUnavailable):
            return ConnectionCheckCompleted(
                "credential_unavailable", self._aware_now()
            )
        if isinstance(snapshot_result, AdminRepositoryFailed):
            return AdminServiceFailed(snapshot_result.code)
        if not isinstance(snapshot_result, SnapshotAvailable):
            return AdminServiceFailed("storage_unavailable")

        snapshot: AdminConnectionSnapshot = snapshot_result.snapshot
        external = self._bot_info_gateway.get_bot_identity(snapshot.access_token)

        try:
            with transaction.atomic(using=self._using):
                final_proof = self._lock_owner(owner)
                if isinstance(final_proof, AdminServiceFailed):
                    return final_proof
                if (
                    final_proof.identity_public_id
                    != initial_proof.identity_public_id
                    or final_proof.provider_id != initial_proof.provider_id
                ):
                    return AdminServiceFailed("owner_operation_blocked")
                revision = self._repository.lock_connection_revision(
                    channel_id,
                    final_proof.provider_id,
                    snapshot.expected_updated_at,
                )
                if isinstance(revision, AdminRepositoryFailed):
                    return AdminServiceFailed(revision.code)
        except PersistenceError as error:
            return AdminServiceFailed(self._storage_code(error))

        if isinstance(external, BotIdentityReceived):
            status = (
                "connected"
                if external.bot_user_id == snapshot.expected_bot_user_id
                else "identity_mismatch"
            )
        else:
            status = external.code
        return ConnectionCheckCompleted(status, self._aware_now())

    def _update_locked(
        self,
        proof: OwnerActiveProof,
        command: UpdateAdminChannel,
        *,
        state_change: bool,
    ):
        result = self._foundation_service.update(
            UpdateLineChannel(
                channel_public_id=command.channel_public_id,
                messaging_api_channel_id=command.messaging_api_channel_id,
                bot_user_id=command.bot_user_id,
                label=command.label,
                credentials=command.credentials,
                provider_id=command.provider_id,
                expected_updated_at=command.expected_updated_at,
                required_provider_id=proof.provider_id,
            )
        )
        if result.status == "failed":
            return AdminServiceFailed(
                self._mutation_code(result.code, state_change=state_change)
            )
        return self._project_mutation(result.channel.public_id, proof.provider_id)

    def _project_mutation(self, public_id: UUID, provider_id: str):
        channel = self._repository.get_for_owner_provider(public_id, provider_id)
        if channel is None:
            raise PersistenceError("storage_unavailable")
        return AdminChannelMutationSucceeded(channel)

    def _lock_owner(self, owner: OwnerOperationContext):
        result = self._owner_fence.lock_active(owner, self._aware_now())
        if result.status == "failed":
            return AdminServiceFailed(result.code)
        return result

    def _aware_now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or timezone.is_naive(now):
            raise TypeError("invalid clock")
        return now

    @staticmethod
    def _storage_code(error: PersistenceError):
        return "storage_retryable" if error.code == "retryable" else "storage_unavailable"

    @staticmethod
    def _mutation_code(code: str, *, state_change: bool = False):
        mapping = {
            "retryable": "storage_retryable",
            "invalid_transition": (
                "credential_unavailable" if state_change else "invalid_input"
            ),
        }
        return mapping.get(code, code)
