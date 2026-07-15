from typing import Protocol, runtime_checkable
from uuid import UUID

from django.db import DatabaseError, OperationalError, transaction
from django.utils import timezone

from .models import LineChannelCredential
from .repositories import PersistenceError, RepositoryProgrammingError
from .types import EncryptedCredential, EncryptedCredentialPair


@runtime_checkable
class RotationCredentialRepository(Protocol):
    def list_credential_public_ids(self) -> tuple[UUID, ...]: ...

    def get_credentials_for_update(
        self, public_id: UUID
    ) -> EncryptedCredentialPair | None: ...

    def replace_credentials_locked(
        self, public_id: UUID, credentials: EncryptedCredentialPair
    ) -> None: ...


class DjangoRotationCredentialRepository:
    _RETRYABLE_DATABASE_CODES = frozenset((1205, 1213))

    def __init__(self, using: str = "default") -> None:
        self.using = using

    def list_credential_public_ids(self) -> tuple[UUID, ...]:
        try:
            values = (
                LineChannelCredential.objects.using(self.using)
                .order_by("line_channel__public_id")
                .values_list("line_channel__public_id", flat=True)
            )
            return tuple(values)
        except OperationalError as error:
            raise self._classify_operational_error(error) from None
        except DatabaseError:
            raise PersistenceError("storage_unavailable") from None

    def get_credentials_for_update(
        self, public_id: UUID
    ) -> EncryptedCredentialPair | None:
        self._require_transaction()
        try:
            credential = (
                LineChannelCredential.objects.using(self.using)
                .select_for_update()
                .filter(line_channel__public_id=public_id)
                .only("access_token_ciphertext", "channel_secret_ciphertext")
                .first()
            )
            if credential is None:
                return None
            return EncryptedCredentialPair(
                EncryptedCredential(bytes(credential.access_token_ciphertext)),
                EncryptedCredential(bytes(credential.channel_secret_ciphertext)),
            )
        except OperationalError as error:
            raise self._classify_operational_error(error) from None
        except DatabaseError:
            raise PersistenceError("storage_unavailable") from None

    def replace_credentials_locked(
        self, public_id: UUID, credentials: EncryptedCredentialPair
    ) -> None:
        self._require_transaction()
        try:
            replaced = (
                LineChannelCredential.objects.using(self.using)
                .filter(line_channel__public_id=public_id)
                .update(
                    access_token_ciphertext=credentials.access_token.ciphertext,
                    channel_secret_ciphertext=credentials.channel_secret.ciphertext,
                    updated_at=timezone.now(),
                )
            )
            if replaced != 1:
                raise PersistenceError("credentials_incomplete")
        except PersistenceError:
            raise
        except OperationalError as error:
            raise self._classify_operational_error(error) from None
        except DatabaseError:
            raise PersistenceError("storage_unavailable") from None

    def _require_transaction(self) -> None:
        if not transaction.get_connection(self.using).in_atomic_block:
            raise RepositoryProgrammingError("transaction_required")

    def _classify_operational_error(self, error: OperationalError) -> PersistenceError:
        code = error.args[0] if error.args else None
        if code in self._RETRYABLE_DATABASE_CODES:
            return PersistenceError("retryable")
        return PersistenceError("storage_unavailable")
