from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from .gateway import (
    InvalidLineProof,
    LinePlatformGateway,
    LinePlatformUnavailable,
    VerifiedLineIdentity,
    VerifyIdentitySucceeded,
)
from .repositories import (
    AccountRepository,
    AccountStateError,
    LineIdentityView,
    LockedOwnerAccount,
    OwnerSessionView,
)
from .runtime import (
    OwnerEligibility,
    OwnerEligibilityDigest,
    derive_owner_digest,
)
from .types import IdToken


@dataclass(frozen=True, slots=True)
class OwnerBindingSucceeded:
    owner: LockedOwnerAccount
    identity: LineIdentityView


@dataclass(frozen=True, slots=True)
class OwnerBindingRejected:
    code: str = "owner_not_allowed"


OwnerBindingResult = OwnerBindingSucceeded | OwnerBindingRejected


@dataclass(frozen=True, slots=True)
class EstablishSessionSucceeded:
    session: OwnerSessionView
    display_name: str
    state: Literal["authenticated", "unlinking"]


@dataclass(frozen=True, slots=True)
class EstablishSessionRejected:
    code: Literal[
        "invalid_line_proof",
        "line_unavailable",
        "owner_not_allowed",
    ]


EstablishSessionResult = EstablishSessionSucceeded | EstablishSessionRejected


@dataclass(frozen=True, slots=True)
class LogoutSucceeded:
    deleted: bool


@dataclass(frozen=True, slots=True)
class AnonymousSessionStatus:
    state: Literal["anonymous"] = "anonymous"


@dataclass(frozen=True, slots=True)
class AuthenticatedSessionStatus:
    session: OwnerSessionView
    display_name: str
    linked: Literal[True] = True
    state: Literal["authenticated"] = "authenticated"


@dataclass(frozen=True, slots=True)
class UnlinkingSessionStatus:
    session: OwnerSessionView
    stage: Literal["deauthorization_pending", "local_deletion_pending"]
    retry_action: Literal["reauthenticate", "retry_local_delete"]
    state: Literal["unlinking"] = "unlinking"


SessionStatus = (
    AnonymousSessionStatus
    | AuthenticatedSessionStatus
    | UnlinkingSessionStatus
)


class DefaultOwnerIdentityBinder:
    def __init__(
        self,
        repository: AccountRepository,
        eligibility: OwnerEligibility,
        *,
        using: str = "default",
    ) -> None:
        self._repository = repository
        self._eligibility = eligibility
        self._using = using

    def bind(self, identity: VerifiedLineIdentity) -> OwnerBindingResult:
        if not self._is_eligible(identity):
            return OwnerBindingRejected()

        try:
            with transaction.atomic(using=self._using):
                return self.bind_in_transaction(identity)
        except AccountStateError as error:
            if error.code == "identity_mismatch":
                return OwnerBindingRejected()
            raise

    def bind_in_transaction(
        self, identity: VerifiedLineIdentity
    ) -> OwnerBindingResult:
        if not self._is_eligible(identity):
            return OwnerBindingRejected()
        owner = self._repository.lock_owner_account()
        stored_identity = self._repository.upsert_identity(identity)
        owner = self._repository.bind_owner_identity(owner, stored_identity.public_id)
        return OwnerBindingSucceeded(owner=owner, identity=stored_identity)

    def _is_eligible(self, identity: VerifiedLineIdentity) -> bool:
        if not isinstance(self._eligibility, OwnerEligibilityDigest):
            return False
        try:
            candidate = derive_owner_digest(
                identity.provider_id,
                identity.subject.reveal_for_identity_binding(),
            )
        except ImproperlyConfigured:
            return False
        return self._eligibility.matches(candidate)


class DefaultAccountSessionService:
    def __init__(
        self,
        gateway: LinePlatformGateway,
        repository: AccountRepository,
        eligibility: OwnerEligibility,
        *,
        using: str = "default",
        session_duration: timedelta = timedelta(hours=8),
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._binder = DefaultOwnerIdentityBinder(
            repository, eligibility, using=using
        )
        self._using = using
        self._session_duration = session_duration

    def establish(
        self, proof: IdToken, now: datetime
    ) -> EstablishSessionResult:
        if timezone.is_naive(now):
            raise ValueError("now must be timezone-aware")
        verification = self._gateway.verify_id_token(proof)
        if isinstance(verification, InvalidLineProof):
            return EstablishSessionRejected("invalid_line_proof")
        if isinstance(verification, LinePlatformUnavailable):
            return EstablishSessionRejected("line_unavailable")
        if not isinstance(verification, VerifyIdentitySucceeded):
            return EstablishSessionRejected("line_unavailable")

        try:
            with transaction.atomic(using=self._using):
                binding = self._binder.bind_in_transaction(verification.identity)
                if isinstance(binding, OwnerBindingRejected):
                    return EstablishSessionRejected("owner_not_allowed")
                session = self._repository.create_owner_session(
                    binding.owner, now + self._session_duration
                )
        except AccountStateError as error:
            if error.code == "identity_mismatch":
                return EstablishSessionRejected("owner_not_allowed")
            raise
        state = (
            "authenticated"
            if binding.owner.state == "active"
            else "unlinking"
        )
        return EstablishSessionSucceeded(
            session=session,
            display_name=binding.identity.display_name,
            state=state,
        )

    def logout(self, owner_session_id: UUID) -> LogoutSucceeded:
        with transaction.atomic(using=self._using):
            deleted = self._repository.delete_owner_session(owner_session_id)
        return LogoutSucceeded(deleted=deleted)

    def get_status(
        self, owner_session_id: UUID | None, now: datetime
    ) -> SessionStatus:
        if timezone.is_naive(now):
            raise ValueError("now must be timezone-aware")
        if owner_session_id is None:
            return AnonymousSessionStatus()
        session = self._repository.get_session(owner_session_id, now)
        if session is None:
            return AnonymousSessionStatus()
        if session.owner_state == "active":
            return AuthenticatedSessionStatus(
                session=session,
                display_name=session.display_name,
            )
        if session.owner_state == "deauthorization_pending":
            return UnlinkingSessionStatus(
                session=session,
                stage="deauthorization_pending",
                retry_action="reauthenticate",
            )
        if session.owner_state == "local_deletion_pending":
            return UnlinkingSessionStatus(
                session=session,
                stage="local_deletion_pending",
                retry_action="retry_local_delete",
            )
        return AnonymousSessionStatus()
