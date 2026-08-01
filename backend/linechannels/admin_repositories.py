from datetime import datetime
from typing import Protocol
from uuid import UUID

from django.db import DatabaseError, OperationalError, transaction
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone

from .admin_types import (
    AdminChannelView,
    AdminConnectionSnapshot,
    AdminConnectionSnapshotResult,
    AdminRepositoryFailed,
    AdminRepositoryUnavailable,
    ConnectionRevisionResult,
    ConnectionRevisionUnchanged,
    SnapshotAvailable,
)
from .crypto import CredentialCryptoError
from .models import LineChannel, LineChannelCredential
from .repositories import PersistenceError, RepositoryProgrammingError
from .types import AccessToken, CredentialContext, EncryptedCredential


class _AccessTokenDecryptor(Protocol):
    def decrypt(
        self, value: EncryptedCredential, context: CredentialContext[AccessToken]
    ) -> AccessToken: ...


class DjangoAdminChannelRepository:
    _RETRYABLE_DATABASE_CODES = frozenset((1205, 1213))

    def __init__(
        self, cipher: _AccessTokenDecryptor, *, using: str = "default"
    ) -> None:
        self._cipher = cipher
        self.using = using

    def list_for_owner_provider(
        self, owner_provider_id: str
    ) -> tuple[AdminChannelView, ...]:
        try:
            rows = self._safe_projection().filter(
                self._provider_scope(owner_provider_id)
            ).order_by("public_id")
            return tuple(self._view(row) for row in rows)
        except OperationalError as error:
            raise self._persistence_error(error) from None
        except DatabaseError:
            raise PersistenceError("storage_unavailable") from None

    def get_for_owner_provider(
        self, public_id: UUID, owner_provider_id: str
    ) -> AdminChannelView | None:
        try:
            row = (
                self._safe_projection()
                .filter(self._provider_scope(owner_provider_id), public_id=public_id)
                .first()
            )
            return None if row is None else self._view(row)
        except OperationalError as error:
            raise self._persistence_error(error) from None
        except DatabaseError:
            raise PersistenceError("storage_unavailable") from None

    def get_connection_snapshot(
        self, public_id: UUID, owner_provider_id: str
    ) -> AdminConnectionSnapshotResult:
        try:
            row = (
                LineChannel.objects.using(self.using)
                .filter(self._provider_scope(owner_provider_id), public_id=public_id)
                .values(
                    "public_id",
                    "bot_user_id",
                    "updated_at",
                    "credential__access_token_ciphertext",
                )
                .first()
            )
        except OperationalError as error:
            code = self._storage_code(error)
            return AdminRepositoryFailed(code)
        except DatabaseError:
            return AdminRepositoryFailed("storage_unavailable")

        if row is None:
            return AdminRepositoryFailed("channel_not_found")
        ciphertext = row["credential__access_token_ciphertext"]
        if not ciphertext:
            return AdminRepositoryUnavailable("credential_unavailable")
        try:
            access_token = self._cipher.decrypt(
                EncryptedCredential(bytes(ciphertext)),
                CredentialContext(public_id, "access_token"),
            )
        except (CredentialCryptoError, TypeError, ValueError):
            return AdminRepositoryUnavailable("credential_unreadable")
        if not isinstance(access_token, AccessToken):
            return AdminRepositoryUnavailable("credential_unreadable")
        return SnapshotAvailable(
            AdminConnectionSnapshot(
                access_token=access_token,
                expected_bot_user_id=row["bot_user_id"],
                expected_updated_at=row["updated_at"],
            )
        )

    def lock_connection_revision(
        self,
        public_id: UUID,
        owner_provider_id: str,
        expected_updated_at: datetime,
    ) -> ConnectionRevisionResult:
        if not transaction.get_connection(self.using).in_atomic_block:
            raise RepositoryProgrammingError("transaction_required")
        if (
            not isinstance(expected_updated_at, datetime)
            or timezone.is_naive(expected_updated_at)
        ):
            raise RepositoryProgrammingError("invalid_revision")
        try:
            row = (
                LineChannel.objects.using(self.using)
                .select_for_update()
                .filter(self._provider_scope(owner_provider_id), public_id=public_id)
                .values("updated_at")
                .first()
            )
        except OperationalError as error:
            return AdminRepositoryFailed(self._storage_code(error))
        except DatabaseError:
            return AdminRepositoryFailed("storage_unavailable")
        if row is None:
            return AdminRepositoryFailed("channel_not_found")
        if row["updated_at"] != expected_updated_at:
            return AdminRepositoryFailed("stale_channel")
        return ConnectionRevisionUnchanged()

    def _safe_projection(self):
        credential_rows = LineChannelCredential.objects.using(self.using).filter(
            line_channel_id=OuterRef("pk")
        )
        complete_credential_rows = credential_rows.exclude(
            access_token_ciphertext=b""
        ).exclude(channel_secret_ciphertext=b"")
        return (
            LineChannel.objects.using(self.using)
            .annotate(
                admin_credentials_configured=Exists(credential_rows),
                admin_credentials_complete=Exists(complete_credential_rows),
                admin_credentials_updated_at=Subquery(
                    credential_rows.values("updated_at")[:1]
                ),
            )
            .values(
                "public_id",
                "messaging_api_channel_id",
                "bot_user_id",
                "label",
                "provider_id",
                "is_active",
                "created_at",
                "updated_at",
                "admin_credentials_configured",
                "admin_credentials_complete",
                "admin_credentials_updated_at",
            )
        )

    @staticmethod
    def _provider_scope(owner_provider_id: str) -> Q:
        return Q(provider_id=owner_provider_id) | Q(provider_id__isnull=True)

    @staticmethod
    def _view(row) -> AdminChannelView:
        configured = bool(row["admin_credentials_complete"])
        return AdminChannelView(
            public_id=row["public_id"],
            messaging_api_channel_id=row["messaging_api_channel_id"],
            bot_user_id=row["bot_user_id"],
            label=row["label"],
            provider_id=row["provider_id"],
            is_active=row["is_active"],
            credentials_state=("configured" if configured else "repair_required"),
            credentials_updated_at=(
                row["admin_credentials_updated_at"]
                if row["admin_credentials_configured"]
                else None
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _persistence_error(self, error: OperationalError) -> PersistenceError:
        code = self._storage_code(error)
        return PersistenceError("retryable" if code == "storage_retryable" else code)

    def _storage_code(self, error: OperationalError):
        code = error.args[0] if error.args else None
        return (
            "storage_retryable"
            if code in self._RETRYABLE_DATABASE_CODES
            else "storage_unavailable"
        )
