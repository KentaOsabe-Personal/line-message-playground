from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .errors import SafeAPIError
from .repositories import (
    AccountPersistenceError,
    AccountRepository,
    DjangoAccountRepository,
    OwnerSessionView,
)


OWNER_SESSION_KEY = "owner_session_id"


@dataclass(frozen=True, slots=True)
class OwnerPrincipal:
    owner_session_id: UUID
    identity_public_id: UUID
    account_state: str

    @property
    def is_authenticated(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class OwnerSessionContext:
    session: OwnerSessionView


class OwnerSessionAuthentication(BaseAuthentication):
    def __init__(
        self,
        repository: AccountRepository | None = None,
        *,
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._repository = repository or DjangoAccountRepository()
        self._clock = clock

    def authenticate(self, request):
        raw_session_id = request.session.get(OWNER_SESSION_KEY)
        if raw_session_id is None:
            return None
        if not isinstance(raw_session_id, str):
            raise AuthenticationFailed()
        try:
            session_id = UUID(raw_session_id)
        except (ValueError, AttributeError, TypeError):
            raise AuthenticationFailed() from None
        if str(session_id) != raw_session_id:
            raise AuthenticationFailed()

        try:
            session = self._repository.get_session(session_id, self._clock())
        except AccountPersistenceError:
            raise SafeAPIError("storage_unavailable") from None
        if session is None:
            raise AuthenticationFailed()
        principal = OwnerPrincipal(
            owner_session_id=session.public_id,
            identity_public_id=session.identity_id,
            account_state=session.owner_state,
        )
        return principal, OwnerSessionContext(session=session)

    def authenticate_header(self, request) -> str:
        return "Session"
