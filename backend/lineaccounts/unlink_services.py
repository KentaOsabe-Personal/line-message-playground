"""LINE deauthorize とローカル削除を収束させる全連携解除 saga。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from linechannels.repositories import LineChannelDirectory

from .authentication import OwnerPrincipal
from .confirmation import UnlinkConfirmation
from .gateway import (
    DeauthorizeRejected,
    DeauthorizeSucceeded,
    DeauthorizeUncertain,
    InvalidLineProof,
    LinePlatformGateway,
    LinePlatformUnavailable,
    VerifyUserTokenSucceeded,
)
from .models import OwnerAccount
from .repositories import (
    AccountPersistenceError,
    AccountRepository,
    AccountStateError,
    LockedOwnerAccount,
)
from .types import UserAccessToken
from .unlink_execution_lock import UnlinkExecutionLock, UnlinkLockError


@dataclass(frozen=True, slots=True)
class UnlinkPreview:
    display_name: str
    recipient_count: int
    channel_labels: tuple[str, ...]
    delivery_audit_retained: bool
    confirmation_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class UnlinkCompleted:
    pass


@dataclass(frozen=True, slots=True)
class UnlinkPendingReauthentication:
    stage: str = "deauthorization_pending"
    retry_action: str = "reauthenticate"


@dataclass(frozen=True, slots=True)
class UnlinkPendingLocalRetry:
    stage: str = "local_deletion_pending"
    retry_action: str = "retry_local_delete"


@dataclass(frozen=True, slots=True)
class UnlinkRejected:
    code: str


@dataclass(frozen=True, slots=True)
class UnlinkPendingMetrics:
    deauthorization_pending_count: int
    local_deletion_pending_count: int
    oldest_deauthorization_pending_seconds: int
    oldest_local_deletion_pending_seconds: int


UnlinkExecutionResult = (
    UnlinkCompleted
    | UnlinkPendingReauthentication
    | UnlinkPendingLocalRetry
    | UnlinkRejected
)


class AccountUnlinkService(Protocol):
    def preview(self, principal: OwnerPrincipal, now: datetime) -> UnlinkPreview: ...

    def execute(
        self,
        principal: OwnerPrincipal,
        confirmation_token: str | None,
        user_access_token: UserAccessToken | None,
        now: datetime,
    ) -> UnlinkExecutionResult: ...


class DefaultAccountUnlinkService:
    def __init__(
        self,
        gateway: LinePlatformGateway,
        repository: AccountRepository,
        execution_lock: UnlinkExecutionLock,
        channel_directory: LineChannelDirectory,
        *,
        confirmation: UnlinkConfirmation | None = None,
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._execution_lock = execution_lock
        self._channel_directory = channel_directory
        self._confirmation = confirmation or UnlinkConfirmation()

    def preview(self, principal: OwnerPrincipal, now: datetime) -> UnlinkPreview:
        with transaction.atomic():
            owner = self._repository.lock_owner_account()
            self._require_principal(owner, principal, active_only=True)
            snapshot = self._repository.get_unlink_snapshot(owner)
        labels = tuple(
            sorted(
                summary.label
                for channel_id in snapshot.channel_ids
                if (summary := self._channel_directory.get(channel_id)) is not None
            )
        )
        return UnlinkPreview(
            display_name=snapshot.display_name,
            recipient_count=len(snapshot.recipient_ids),
            channel_labels=labels,
            delivery_audit_retained=True,
            confirmation_token=self._confirmation.issue(snapshot, now),
            expires_at=now + timedelta(minutes=5),
        )

    def execute(
        self,
        principal: OwnerPrincipal,
        confirmation_token: str | None,
        user_access_token: UserAccessToken | None,
        now: datetime,
    ) -> UnlinkExecutionResult:
        try:
            owner = self._read_owner()
            self._require_principal(owner, principal, active_only=False)
        except AccountStateError as error:
            return UnlinkRejected(self._safe_state_code(error))
        except AccountPersistenceError:
            return UnlinkRejected("storage_unavailable")

        if not self._valid_request_shape(
            owner.state, confirmation_token, user_access_token
        ):
            return UnlinkRejected("validation_error")

        if owner.state == OwnerAccount.State.LOCAL_DELETION_PENDING:
            assert owner.unlink_generation is not None
            return self._finalize(owner.unlink_generation)
        if owner.state == OwnerAccount.State.ACTIVE:
            return self._start(principal, confirmation_token, user_access_token, now)
        if owner.state == OwnerAccount.State.DEAUTHORIZATION_PENDING:
            assert owner.unlink_generation is not None
            return self._resume_deauthorization(
                principal, owner.unlink_generation, user_access_token, now
            )
        return UnlinkRejected("unlink_attempt_stale")

    def pending_metrics(self, now: datetime) -> UnlinkPendingMetrics:
        if timezone.is_naive(now):
            raise ValueError("aware datetime required")
        rows = tuple(
            OwnerAccount.objects.filter(
                state__in=(
                    OwnerAccount.State.DEAUTHORIZATION_PENDING,
                    OwnerAccount.State.LOCAL_DELETION_PENDING,
                )
            ).values_list("state", "updated_at")
        )
        ages: dict[str, list[int]] = {
            OwnerAccount.State.DEAUTHORIZATION_PENDING: [],
            OwnerAccount.State.LOCAL_DELETION_PENDING: [],
        }
        for state, updated_at in rows:
            ages[state].append(max(0, int((now - updated_at).total_seconds())))
        deauthorization = ages[OwnerAccount.State.DEAUTHORIZATION_PENDING]
        local = ages[OwnerAccount.State.LOCAL_DELETION_PENDING]
        return UnlinkPendingMetrics(
            deauthorization_pending_count=len(deauthorization),
            local_deletion_pending_count=len(local),
            oldest_deauthorization_pending_seconds=max(deauthorization, default=0),
            oldest_local_deletion_pending_seconds=max(local, default=0),
        )

    def _start(
        self,
        principal: OwnerPrincipal,
        confirmation_token: str | None,
        token: UserAccessToken | None,
        now: datetime,
    ) -> UnlinkExecutionResult:
        if (
            confirmation_token is None
            or not self._confirmation.precheck(confirmation_token, now)
        ):
            return UnlinkRejected("stale_confirmation")
        proof = self._verify_token(principal.identity_public_id, token)
        if proof is not None:
            return proof
        try:
            with transaction.atomic():
                owner = self._repository.lock_owner_account()
                self._require_principal(owner, principal, active_only=True)
                snapshot = self._repository.get_unlink_snapshot(owner)
                if not self._confirmation.verify(confirmation_token, snapshot, now):
                    return UnlinkRejected("stale_confirmation")
                generation = uuid4()
                self._repository.begin_unlink(owner, generation)
        except AccountStateError as error:
            return UnlinkRejected(self._safe_state_code(error))
        except AccountPersistenceError:
            return UnlinkRejected("storage_unavailable")
        assert token is not None
        return self._deauthorize(generation, token, now)

    def _resume_deauthorization(
        self,
        principal: OwnerPrincipal,
        generation: UUID,
        token: UserAccessToken | None,
        now: datetime,
    ) -> UnlinkExecutionResult:
        proof = self._verify_token(principal.identity_public_id, token)
        if proof is not None:
            return proof
        assert token is not None
        return self._deauthorize(generation, token, now)

    def _verify_token(
        self, identity_id: UUID, token: UserAccessToken | None
    ) -> UnlinkRejected | None:
        if token is None:
            return UnlinkRejected("invalid_line_proof")
        try:
            identity = self._repository.get_identity(identity_id)
        except AccountPersistenceError:
            return UnlinkRejected("storage_unavailable")
        if identity is None:
            return UnlinkRejected("owner_not_allowed")
        result = self._gateway.verify_user_access_token(token, identity.subject)
        if isinstance(result, VerifyUserTokenSucceeded):
            return None
        if isinstance(result, LinePlatformUnavailable):
            return UnlinkRejected(
                "line_rate_limited" if result.rate_limited else "line_unavailable"
            )
        assert isinstance(result, InvalidLineProof)
        return UnlinkRejected("invalid_line_proof")

    def _deauthorize(
        self, generation: UUID, token: UserAccessToken, now: datetime
    ) -> UnlinkExecutionResult:
        try:
            with self._execution_lock.acquire(1) as acquired:
                if not acquired:
                    return UnlinkRejected("unlink_in_progress")
                try:
                    owner = self._read_owner()
                except AccountPersistenceError:
                    return UnlinkRejected("storage_unavailable")
                if owner.unlink_generation != generation:
                    return UnlinkRejected("unlink_attempt_stale")
                if owner.state == OwnerAccount.State.LOCAL_DELETION_PENDING:
                    return self._finalize(generation)
                if owner.state != OwnerAccount.State.DEAUTHORIZATION_PENDING:
                    return UnlinkRejected("unlink_attempt_stale")

                result = self._gateway.deauthorize(token)
                if isinstance(result, LinePlatformUnavailable):
                    return UnlinkRejected(
                        "line_rate_limited"
                        if result.rate_limited
                        else "line_unavailable"
                    )
                if isinstance(result, (DeauthorizeRejected, DeauthorizeUncertain)):
                    return UnlinkPendingReauthentication()
                assert isinstance(result, DeauthorizeSucceeded)
                try:
                    with transaction.atomic():
                        locked = self._repository.lock_owner_account()
                        self._repository.mark_line_deauthorized(
                            locked, generation, now
                        )
                except (AccountPersistenceError, AccountStateError):
                    return UnlinkPendingReauthentication()
                return self._finalize(generation)
        except UnlinkLockError:
            return UnlinkRejected("storage_unavailable")

    def _finalize(self, generation: UUID) -> UnlinkExecutionResult:
        try:
            with transaction.atomic():
                owner = self._repository.lock_owner_account()
                if owner.state == OwnerAccount.State.VACANT:
                    return UnlinkCompleted()
                self._repository.finalize_unlink(owner, generation)
            return UnlinkCompleted()
        except AccountStateError as error:
            return UnlinkRejected(self._safe_state_code(error))
        except AccountPersistenceError:
            return UnlinkPendingLocalRetry()

    def _read_owner(self) -> LockedOwnerAccount:
        with transaction.atomic():
            return self._repository.lock_owner_account()

    @staticmethod
    def _require_principal(
        owner: LockedOwnerAccount,
        principal: OwnerPrincipal,
        *,
        active_only: bool,
    ) -> None:
        if owner.identity_id != principal.identity_public_id:
            raise AccountStateError("identity_mismatch")
        if active_only and owner.state != OwnerAccount.State.ACTIVE:
            raise AccountStateError("owner_not_active")
        if not active_only and owner.state not in (
            OwnerAccount.State.ACTIVE,
            OwnerAccount.State.DEAUTHORIZATION_PENDING,
            OwnerAccount.State.LOCAL_DELETION_PENDING,
        ):
            raise AccountStateError("invalid_unlink_stage")

    @staticmethod
    def _safe_state_code(error: AccountStateError) -> str:
        return {
            "identity_mismatch": "owner_not_allowed",
            "owner_not_active": "unlink_in_progress",
            "unlink_attempt_stale": "unlink_attempt_stale",
        }.get(error.code, "unlink_attempt_stale")

    @staticmethod
    def _valid_request_shape(
        state: str,
        confirmation_token: str | None,
        user_access_token: UserAccessToken | None,
    ) -> bool:
        if state == OwnerAccount.State.ACTIVE:
            return confirmation_token is not None and user_access_token is not None
        if state == OwnerAccount.State.DEAUTHORIZATION_PENDING:
            return confirmation_token is None and user_access_token is not None
        if state == OwnerAccount.State.LOCAL_DELETION_PENDING:
            return confirmation_token is None and user_access_token is None
        return False
