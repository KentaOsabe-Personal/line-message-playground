from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from .models import OwnerAccount
from .repositories import (
    AccountPersistenceError,
    AccountRepository,
    AccountRepositoryProgrammingError,
)


@dataclass(frozen=True, slots=True)
class OwnerOperationContext:
    owner_session_id: UUID
    identity_public_id: UUID


@dataclass(frozen=True, slots=True)
class OwnerActiveProof:
    identity_public_id: UUID
    provider_id: str
    status: Literal["active"] = "active"


OwnerFenceFailureCode = Literal[
    "authentication_required",
    "owner_operation_blocked",
    "storage_retryable",
    "storage_unavailable",
]


@dataclass(frozen=True, slots=True)
class OwnerFenceFailed:
    code: OwnerFenceFailureCode
    status: Literal["failed"] = "failed"


OwnerFenceResult = OwnerActiveProof | OwnerFenceFailed


class OwnerOperationFence(Protocol):
    def lock_active(
        self, context: OwnerOperationContext, now: datetime
    ) -> OwnerFenceResult: ...


class DjangoOwnerOperationFence:
    def __init__(self, repository: AccountRepository, *, using: str = "default") -> None:
        self._repository = repository
        self._using = using

    def lock_active(
        self, context: OwnerOperationContext, now: datetime
    ) -> OwnerFenceResult:
        if (
            not isinstance(context, OwnerOperationContext)
            or not isinstance(now, datetime)
            or timezone.is_naive(now)
            or not transaction.get_connection(self._using).in_atomic_block
        ):
            raise AccountRepositoryProgrammingError("invalid_command")
        try:
            owner = self._repository.lock_owner_account()
            if owner.state != OwnerAccount.State.ACTIVE or owner.identity_id is None:
                return OwnerFenceFailed("owner_operation_blocked")
            session = self._repository.lock_owner_session(
                owner,
                context.owner_session_id,
                context.identity_public_id,
                now,
            )
            if session is None:
                return OwnerFenceFailed("authentication_required")
            if session.owner_state != OwnerAccount.State.ACTIVE:
                return OwnerFenceFailed("owner_operation_blocked")
            return OwnerActiveProof(session.identity_id, session.provider_id)
        except AccountPersistenceError as error:
            code = (
                "storage_retryable"
                if error.code == "retryable"
                else "storage_unavailable"
            )
            return OwnerFenceFailed(code)
