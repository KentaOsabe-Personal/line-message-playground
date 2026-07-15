from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.utils import timezone

from .crypto import CredentialCryptoError
from .models import LineChannel, LineChannelCredential
from .types import (
    AccessToken,
    ChannelSecret,
    CredentialAvailable,
    CredentialContext,
    CredentialUnavailable,
    EncryptedCredential,
    EncryptedCredentialPair,
    PublicChannelSummary,
    SecretT,
)


@dataclass(frozen=True)
class NewLineChannel:
    public_id: UUID
    messaging_api_channel_id: str
    bot_user_id: str
    label: str
    is_active: bool


@dataclass(frozen=True, repr=False)
class PersistedChannelMutation:
    messaging_api_channel_id: str | None = None
    bot_user_id: str | None = None
    label: str | None = None
    is_active: bool | None = None
    encrypted_credentials: EncryptedCredentialPair | None = None

    def __repr__(self) -> str:
        fields = (
            "messaging_api_channel_id",
            "bot_user_id",
            "label",
            "is_active",
        )
        specified = ", ".join(
            field_name for field_name in fields if getattr(self, field_name) is not None
        )
        credentials = self.encrypted_credentials is not None
        return (
            "<PersistedChannelMutation "
            f"fields=[{specified}] encrypted_credentials={credentials}>"
        )


@dataclass(frozen=True, repr=False)
class LockedChannel:
    public: PublicChannelSummary
    encrypted_credentials: EncryptedCredentialPair | None

    def __repr__(self) -> str:
        return (
            f"<LockedChannel public_id={self.public.public_id} "
            f"active={self.public.is_active} "
            f"credentials_configured={self.encrypted_credentials is not None}>"
        )


@runtime_checkable
class LineChannelRepository(Protocol):
    def create_with_credentials(
        self,
        channel: NewLineChannel,
        credentials: EncryptedCredentialPair,
    ) -> PublicChannelSummary: ...

    def get_for_update(self, public_id: UUID) -> LockedChannel | None: ...

    def update_locked(
        self,
        channel: LockedChannel,
        mutation: PersistedChannelMutation,
    ) -> PublicChannelSummary: ...


@runtime_checkable
class CredentialRepository(Protocol):
    def get_access_token(
        self,
        channel_public_id: UUID,
    ) -> CredentialAvailable[AccessToken] | CredentialUnavailable: ...

    def get_channel_secret(
        self,
        channel_public_id: UUID,
    ) -> CredentialAvailable[ChannelSecret] | CredentialUnavailable: ...


class _CredentialDecryptor(Protocol):
    def decrypt(
        self,
        value: EncryptedCredential,
        context: CredentialContext[SecretT],
    ) -> SecretT: ...


PersistenceFailureCode = Literal[
    "unique_conflict",
    "retryable",
    "storage_unavailable",
    "credentials_incomplete",
]


class PersistenceError(RuntimeError):
    def __init__(self, code: PersistenceFailureCode) -> None:
        self.code = code
        super().__init__(code)


class RepositoryProgrammingError(RuntimeError):
    def __init__(self, code: Literal["transaction_required", "empty_mutation"]) -> None:
        self.code = code
        super().__init__(code)


class DjangoLineChannelRepository:
    _RETRYABLE_DATABASE_CODES = frozenset((1205, 1213))

    def __init__(self, using: str = "default") -> None:
        self.using = using

    def create_with_credentials(
        self,
        channel: NewLineChannel,
        credentials: EncryptedCredentialPair,
    ) -> PublicChannelSummary:
        self._require_transaction()
        try:
            stored = LineChannel.objects.using(self.using).create(
                public_id=channel.public_id,
                messaging_api_channel_id=channel.messaging_api_channel_id,
                bot_user_id=channel.bot_user_id,
                label=channel.label,
                is_active=channel.is_active,
            )
            LineChannelCredential.objects.using(self.using).create(
                line_channel=stored,
                access_token_ciphertext=credentials.access_token.ciphertext,
                channel_secret_ciphertext=credentials.channel_secret.ciphertext,
            )
            return self._summary(stored, credentials_configured=True)
        except IntegrityError:
            raise PersistenceError("unique_conflict") from None
        except OperationalError as error:
            raise self._classify_operational_error(error) from None
        except DatabaseError:
            raise PersistenceError("storage_unavailable") from None

    def get_for_update(self, public_id: UUID) -> LockedChannel | None:
        self._require_transaction()
        try:
            channel = (
                LineChannel.objects.using(self.using)
                .select_for_update()
                .select_related("credential")
                .filter(public_id=public_id)
                .first()
            )
            if channel is None:
                return None
            try:
                credential = channel.credential
            except LineChannelCredential.DoesNotExist:
                encrypted_credentials = None
            else:
                encrypted_credentials = EncryptedCredentialPair(
                    EncryptedCredential(bytes(credential.access_token_ciphertext)),
                    EncryptedCredential(bytes(credential.channel_secret_ciphertext)),
                )
            return LockedChannel(
                public=self._summary(
                    channel,
                    credentials_configured=encrypted_credentials is not None,
                ),
                encrypted_credentials=encrypted_credentials,
            )
        except OperationalError as error:
            raise self._classify_operational_error(error) from None
        except DatabaseError:
            raise PersistenceError("storage_unavailable") from None

    def update_locked(
        self,
        channel: LockedChannel,
        mutation: PersistedChannelMutation,
    ) -> PublicChannelSummary:
        self._require_transaction()
        if not self._has_mutation(mutation):
            raise RepositoryProgrammingError("empty_mutation")

        try:
            stored = (
                LineChannel.objects.using(self.using)
                .select_for_update()
                .get(public_id=channel.public.public_id)
            )
            update_fields: list[str] = []
            for field_name in (
                "messaging_api_channel_id",
                "bot_user_id",
                "label",
                "is_active",
            ):
                value = getattr(mutation, field_name)
                if value is not None:
                    setattr(stored, field_name, value)
                    update_fields.append(field_name)

            now = timezone.now()
            stored.updated_at = now
            stored.save(using=self.using, update_fields=(*update_fields, "updated_at"))

            credentials_configured = channel.encrypted_credentials is not None
            if mutation.encrypted_credentials is not None:
                replaced = (
                    LineChannelCredential.objects.using(self.using)
                    .filter(line_channel=stored)
                    .update(
                        access_token_ciphertext=(
                            mutation.encrypted_credentials.access_token.ciphertext
                        ),
                        channel_secret_ciphertext=(
                            mutation.encrypted_credentials.channel_secret.ciphertext
                        ),
                        updated_at=now,
                    )
                )
                if replaced != 1:
                    raise PersistenceError("credentials_incomplete")
                credentials_configured = True

            return self._summary(
                stored,
                credentials_configured=credentials_configured,
            )
        except IntegrityError:
            raise PersistenceError("unique_conflict") from None
        except OperationalError as error:
            raise self._classify_operational_error(error) from None
        except DatabaseError:
            raise PersistenceError("storage_unavailable") from None

    def _require_transaction(self) -> None:
        if not transaction.get_connection(self.using).in_atomic_block:
            raise RepositoryProgrammingError("transaction_required")

    @staticmethod
    def _has_mutation(mutation: PersistedChannelMutation) -> bool:
        return any(
            (
                mutation.messaging_api_channel_id is not None,
                mutation.bot_user_id is not None,
                mutation.label is not None,
                mutation.is_active is not None,
                mutation.encrypted_credentials is not None,
            )
        )

    def _classify_operational_error(self, error: OperationalError) -> PersistenceError:
        code = error.args[0] if error.args else None
        if code in self._RETRYABLE_DATABASE_CODES:
            return PersistenceError("retryable")
        return PersistenceError("storage_unavailable")

    @staticmethod
    def _summary(
        channel: LineChannel,
        *,
        credentials_configured: bool,
    ) -> PublicChannelSummary:
        return PublicChannelSummary(
            public_id=channel.public_id,
            messaging_api_channel_id=channel.messaging_api_channel_id,
            bot_user_id=channel.bot_user_id,
            label=channel.label,
            is_active=channel.is_active,
            credentials_configured=credentials_configured,
            created_at=channel.created_at,
            updated_at=channel.updated_at,
        )


class DjangoCredentialRepository:
    def __init__(
        self,
        cipher: _CredentialDecryptor,
        using: str = "default",
    ) -> None:
        self._cipher = cipher
        self.using = using

    def get_access_token(
        self,
        channel_public_id: UUID,
    ) -> CredentialAvailable[AccessToken] | CredentialUnavailable:
        return self._get_credential(
            channel_public_id=channel_public_id,
            ciphertext_field="credential__access_token_ciphertext",
            kind="access_token",
            expected_type=AccessToken,
        )

    def get_channel_secret(
        self,
        channel_public_id: UUID,
    ) -> CredentialAvailable[ChannelSecret] | CredentialUnavailable:
        return self._get_credential(
            channel_public_id=channel_public_id,
            ciphertext_field="credential__channel_secret_ciphertext",
            kind="channel_secret",
            expected_type=ChannelSecret,
        )

    def _get_credential(
        self,
        *,
        channel_public_id: UUID,
        ciphertext_field: str,
        kind: Literal["access_token", "channel_secret"],
        expected_type: type[SecretT],
    ) -> CredentialAvailable[SecretT] | CredentialUnavailable:
        try:
            row = (
                LineChannel.objects.using(self.using)
                .filter(public_id=channel_public_id)
                .values("is_active", ciphertext_field)
                .first()
            )
        except DatabaseError:
            return CredentialUnavailable("credential_unreadable")

        if row is None:
            return CredentialUnavailable("channel_not_found")
        if not row["is_active"]:
            return CredentialUnavailable("channel_inactive")

        ciphertext = row[ciphertext_field]
        if ciphertext is None:
            return CredentialUnavailable("credentials_incomplete")

        try:
            encrypted = EncryptedCredential(bytes(ciphertext))
            context = CredentialContext(
                channel_public_id=channel_public_id,
                kind=kind,
            )
            secret = self._cipher.decrypt(encrypted, context)
        except (CredentialCryptoError, TypeError, ValueError):
            return CredentialUnavailable("credential_unreadable")

        if not isinstance(secret, expected_type):
            return CredentialUnavailable("credential_unreadable")
        return CredentialAvailable(secret)
