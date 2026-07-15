from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from .crypto import CredentialCryptoError
from .types import (
    AccessToken,
    ChannelSecret,
    CredentialContext,
    EncryptedCredential,
    EncryptedCredentialPair,
    SecretT,
)


RotationItemFailureCode = Literal["credential_unreadable", "verification_failed"]


@dataclass(frozen=True)
class RotationItemVerified:
    status: Literal["verified"] = "verified"


@dataclass(frozen=True, repr=False)
class RotationItemRotated:
    credentials: EncryptedCredentialPair
    status: Literal["rotated"] = "rotated"

    def __repr__(self) -> str:
        return "<RotationItemRotated credentials=redacted>"


@dataclass(frozen=True)
class RotationItemFailed:
    code: RotationItemFailureCode
    status: Literal["failed"] = "failed"


RotationItemResult = RotationItemVerified | RotationItemRotated | RotationItemFailed


@dataclass(frozen=True)
class PrimaryVerificationVerified:
    status: Literal["verified"] = "verified"


@dataclass(frozen=True)
class PrimaryVerificationFailed:
    code: RotationItemFailureCode
    status: Literal["failed"] = "failed"


PrimaryVerificationResult = PrimaryVerificationVerified | PrimaryVerificationFailed


class _CredentialCipher(Protocol):
    def decrypt(
        self, value: EncryptedCredential, context: CredentialContext[SecretT]
    ) -> SecretT: ...

    def decrypt_with_primary(
        self, value: EncryptedCredential, context: CredentialContext[SecretT]
    ) -> SecretT: ...

    def rotate(
        self, value: EncryptedCredential, context: CredentialContext[SecretT]
    ) -> EncryptedCredential: ...


@runtime_checkable
class CredentialRotationItemProcessor(Protocol):
    def process(
        self, public_id: UUID, credentials: EncryptedCredentialPair
    ) -> RotationItemResult: ...

    def verify_with_primary(
        self, public_id: UUID, credentials: EncryptedCredentialPair
    ) -> PrimaryVerificationResult: ...


class DefaultCredentialRotationItemProcessor:
    def __init__(self, cipher: _CredentialCipher) -> None:
        self._cipher = cipher

    def process(
        self, public_id: UUID, credentials: EncryptedCredentialPair
    ) -> RotationItemResult:
        contexts = self._contexts(public_id)
        try:
            self._decrypt_pair(credentials, contexts, primary_only=True)
        except Exception:
            pass
        else:
            return RotationItemVerified()

        try:
            original = self._decrypt_pair(credentials, contexts, primary_only=False)
        except Exception:
            return RotationItemFailed("credential_unreadable")

        try:
            rotated = EncryptedCredentialPair(
                self._cipher.rotate(credentials.access_token, contexts[0]),
                self._cipher.rotate(credentials.channel_secret, contexts[1]),
            )
            verified = self._decrypt_pair(rotated, contexts, primary_only=True)
            if (
                verified[0].reveal_for_use() != original[0].reveal_for_use()
                or verified[1].reveal_for_use() != original[1].reveal_for_use()
            ):
                raise CredentialCryptoError("credential_unreadable")
        except Exception:
            return RotationItemFailed("verification_failed")
        return RotationItemRotated(rotated)

    def verify_with_primary(
        self, public_id: UUID, credentials: EncryptedCredentialPair
    ) -> PrimaryVerificationResult:
        try:
            self._decrypt_pair(
                credentials,
                self._contexts(public_id),
                primary_only=True,
            )
        except Exception:
            return PrimaryVerificationFailed("credential_unreadable")
        return PrimaryVerificationVerified()

    def _decrypt_pair(self, credentials, contexts, *, primary_only):
        decrypt = (
            self._cipher.decrypt_with_primary
            if primary_only
            else self._cipher.decrypt
        )
        access_token = decrypt(credentials.access_token, contexts[0])
        channel_secret = decrypt(credentials.channel_secret, contexts[1])
        if not isinstance(access_token, AccessToken) or not isinstance(
            channel_secret, ChannelSecret
        ):
            raise CredentialCryptoError("credential_unreadable")
        return access_token, channel_secret

    @staticmethod
    def _contexts(public_id: UUID):
        return (
            CredentialContext[AccessToken](public_id, "access_token"),
            CredentialContext[ChannelSecret](public_id, "channel_secret"),
        )
