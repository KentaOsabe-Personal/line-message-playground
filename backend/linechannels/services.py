import uuid
from collections.abc import Callable
from typing import Protocol

from django.db import transaction

from .crypto import CredentialCryptoError
from .repositories import (
    LineChannelRepository,
    NewLineChannel,
    PersistedChannelMutation,
    PersistenceError,
)
from .types import (
    AccessToken,
    ChannelMutationFailed,
    ChannelMutationResult,
    ChannelMutationSucceeded,
    ChannelSecret,
    CredentialContext,
    EncryptedCredential,
    EncryptedCredentialPair,
    RegisterLineChannel,
    SecretT,
    UpdateLineChannel,
)
from .validators import (
    BoundaryValidationError,
    validate_bot_user_id,
    validate_credential_pair,
    validate_label,
    validate_messaging_api_channel_id,
    validate_public_id,
)


class _CredentialCipher(Protocol):
    def encrypt(
        self,
        value: SecretT,
        context: CredentialContext[SecretT],
    ) -> EncryptedCredential: ...

    def decrypt(
        self,
        value: EncryptedCredential,
        context: CredentialContext[SecretT],
    ) -> SecretT: ...

    def decrypt_with_primary(
        self,
        value: EncryptedCredential,
        context: CredentialContext[SecretT],
    ) -> SecretT: ...


class LineChannelService(Protocol):
    def register(self, command: RegisterLineChannel) -> ChannelMutationResult: ...

    def update(self, command: UpdateLineChannel) -> ChannelMutationResult: ...

    def set_active(
        self, channel_public_id: uuid.UUID, active: bool
    ) -> ChannelMutationResult: ...


class DefaultLineChannelService:
    def __init__(
        self,
        repository: LineChannelRepository,
        cipher: _CredentialCipher,
        *,
        using: str = "default",
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._using = using
        self._uuid_factory = uuid_factory

    def register(self, command: RegisterLineChannel) -> ChannelMutationResult:
        try:
            validated = self._validate_register(command)
            public_id = self._uuid_factory()
            encrypted = self._encrypt_pair(public_id, validated.credentials)
            channel = NewLineChannel(
                public_id=public_id,
                messaging_api_channel_id=validated.messaging_api_channel_id,
                bot_user_id=validated.bot_user_id,
                label=validated.label,
                is_active=validated.is_active,
            )
        except BoundaryValidationError:
            return ChannelMutationFailed("invalid_input")
        except CredentialCryptoError:
            return ChannelMutationFailed("encryption_failed")

        try:
            with transaction.atomic(using=self._using):
                summary = self._repository.create_with_credentials(channel, encrypted)
        except PersistenceError as error:
            return ChannelMutationFailed(self._persistence_failure(error))
        return ChannelMutationSucceeded(summary)

    def update(self, command: UpdateLineChannel) -> ChannelMutationResult:
        try:
            validated = self._validate_update(command)
        except BoundaryValidationError:
            return ChannelMutationFailed("invalid_input")

        try:
            with transaction.atomic(using=self._using):
                locked = self._repository.get_for_update(
                    validated.channel_public_id
                )
                if locked is None:
                    return ChannelMutationFailed("channel_not_found")

                encrypted = None
                if validated.credentials is not None:
                    encrypted = self._encrypt_pair(
                        validated.channel_public_id,
                        validated.credentials,
                    )

                if validated.is_active is True:
                    if encrypted is not None:
                        self._verify_new_pair(
                            validated.channel_public_id,
                            validated.credentials,
                            encrypted,
                        )
                    elif locked.encrypted_credentials is not None:
                        self._verify_saved_pair(
                            validated.channel_public_id,
                            locked.encrypted_credentials,
                        )
                    else:
                        return ChannelMutationFailed("invalid_transition")

                summary = self._repository.update_locked(
                    locked,
                    PersistedChannelMutation(
                        messaging_api_channel_id=(
                            validated.messaging_api_channel_id
                        ),
                        bot_user_id=validated.bot_user_id,
                        label=validated.label,
                        is_active=validated.is_active,
                        encrypted_credentials=encrypted,
                    ),
                )
        except CredentialCryptoError as error:
            code = (
                "encryption_failed"
                if error.code == "encryption_failed"
                else "credential_unreadable"
            )
            return ChannelMutationFailed(code)
        except PersistenceError as error:
            return ChannelMutationFailed(self._persistence_failure(error))
        return ChannelMutationSucceeded(summary)

    def set_active(
        self, channel_public_id: uuid.UUID, active: bool
    ) -> ChannelMutationResult:
        return self.update(UpdateLineChannel(channel_public_id, is_active=active))

    @staticmethod
    def _validate_register(command: RegisterLineChannel) -> RegisterLineChannel:
        if not isinstance(command, RegisterLineChannel) or type(command.is_active) is not bool:
            raise BoundaryValidationError()
        return RegisterLineChannel(
            messaging_api_channel_id=validate_messaging_api_channel_id(
                command.messaging_api_channel_id
            ),
            bot_user_id=validate_bot_user_id(command.bot_user_id),
            label=validate_label(command.label),
            credentials=validate_credential_pair(command.credentials),
            is_active=command.is_active,
        )

    @staticmethod
    def _validate_update(command: UpdateLineChannel) -> UpdateLineChannel:
        if not isinstance(command, UpdateLineChannel):
            raise BoundaryValidationError()
        if not any(
            value is not None
            for value in (
                command.messaging_api_channel_id,
                command.bot_user_id,
                command.label,
                command.credentials,
                command.is_active,
            )
        ):
            raise BoundaryValidationError()
        if command.is_active is not None and type(command.is_active) is not bool:
            raise BoundaryValidationError()
        return UpdateLineChannel(
            channel_public_id=validate_public_id(command.channel_public_id),
            messaging_api_channel_id=(
                validate_messaging_api_channel_id(command.messaging_api_channel_id)
                if command.messaging_api_channel_id is not None
                else None
            ),
            bot_user_id=(
                validate_bot_user_id(command.bot_user_id)
                if command.bot_user_id is not None
                else None
            ),
            label=(validate_label(command.label) if command.label is not None else None),
            credentials=(
                validate_credential_pair(command.credentials)
                if command.credentials is not None
                else None
            ),
            is_active=command.is_active,
        )

    def _encrypt_pair(
        self,
        public_id: uuid.UUID,
        credentials,
    ) -> EncryptedCredentialPair:
        access_context = CredentialContext[AccessToken](public_id, "access_token")
        secret_context = CredentialContext[ChannelSecret](public_id, "channel_secret")
        return EncryptedCredentialPair(
            self._cipher.encrypt(credentials.access_token, access_context),
            self._cipher.encrypt(credentials.channel_secret, secret_context),
        )

    def _verify_saved_pair(
        self,
        public_id: uuid.UUID,
        encrypted: EncryptedCredentialPair,
    ) -> None:
        access_context = CredentialContext[AccessToken](public_id, "access_token")
        secret_context = CredentialContext[ChannelSecret](public_id, "channel_secret")
        access_token = self._cipher.decrypt(encrypted.access_token, access_context)
        channel_secret = self._cipher.decrypt(encrypted.channel_secret, secret_context)
        if not isinstance(access_token, AccessToken) or not isinstance(
            channel_secret, ChannelSecret
        ):
            raise CredentialCryptoError("credential_unreadable")

    def _verify_new_pair(
        self,
        public_id: uuid.UUID,
        plaintext,
        encrypted: EncryptedCredentialPair,
    ) -> None:
        access_context = CredentialContext[AccessToken](public_id, "access_token")
        secret_context = CredentialContext[ChannelSecret](public_id, "channel_secret")
        access_token = self._cipher.decrypt_with_primary(
            encrypted.access_token, access_context
        )
        channel_secret = self._cipher.decrypt_with_primary(
            encrypted.channel_secret, secret_context
        )
        if (
            not isinstance(access_token, AccessToken)
            or not isinstance(channel_secret, ChannelSecret)
            or access_token.reveal_for_use()
            != plaintext.access_token.reveal_for_use()
            or channel_secret.reveal_for_use()
            != plaintext.channel_secret.reveal_for_use()
        ):
            raise CredentialCryptoError("credential_unreadable")

    @staticmethod
    def _persistence_failure(error: PersistenceError):
        return {
            "unique_conflict": "duplicate_channel",
            "retryable": "retryable",
            "storage_unavailable": "storage_unavailable",
            "credentials_incomplete": "storage_unavailable",
        }[error.code]
