import base64
import binascii
import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .types import (
    AccessToken,
    ChannelSecret,
    CredentialContext,
    EncryptedCredential,
    RotationReadiness,
    SecretT,
)


class CredentialKeyringConfigurationError(RuntimeError):
    pass


class CredentialCryptoError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class FernetCredentialCipher:
    def __init__(self, keyring: "ValidatedCredentialKeyring") -> None:
        raw_keys = keyring._ValidatedCredentialKeyring__keys
        self.__fernets = tuple(
            Fernet(base64.urlsafe_b64encode(raw_key)) for raw_key in raw_keys
        )
        self.__multi_fernet = MultiFernet(list(self.__fernets))

    def rotation_readiness(self) -> RotationReadiness:
        return "ready" if len(self.__fernets) > 1 else "old_key_missing"

    def encrypt(
        self,
        value: SecretT,
        context: CredentialContext[SecretT],
    ) -> EncryptedCredential:
        try:
            plaintext = self._secret_value(value, context)
            envelope = json.dumps(
                {
                    "format_version": 1,
                    "channel_public_id": str(context.channel_public_id),
                    "credential_kind": context.kind,
                    "value": plaintext,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return EncryptedCredential(self.__fernets[0].encrypt(envelope))
        except CredentialCryptoError:
            raise
        except Exception:
            raise CredentialCryptoError("encryption_failed") from None

    def decrypt(
        self,
        value: EncryptedCredential,
        context: CredentialContext[SecretT],
    ) -> SecretT:
        return self._decrypt_with(self.__multi_fernet, value, context)

    def decrypt_with_primary(
        self,
        value: EncryptedCredential,
        context: CredentialContext[SecretT],
    ) -> SecretT:
        return self._decrypt_with(self.__fernets[0], value, context)

    def rotate(
        self,
        value: EncryptedCredential,
        context: CredentialContext[SecretT],
    ) -> EncryptedCredential:
        plaintext = self.decrypt(value, context)
        return self.encrypt(plaintext, context)

    def _decrypt_with(
        self,
        decryptor: Fernet | MultiFernet,
        value: EncryptedCredential,
        context: CredentialContext[SecretT],
    ) -> SecretT:
        try:
            if not isinstance(value, EncryptedCredential):
                raise ValueError
            envelope_bytes = decryptor.decrypt(value.ciphertext)
            envelope = json.loads(envelope_bytes.decode("utf-8"))
            plaintext = self._validate_envelope(envelope, context)
            if context.kind == "access_token":
                return AccessToken(plaintext)  # type: ignore[return-value]
            return ChannelSecret(plaintext)  # type: ignore[return-value]
        except (InvalidToken, AttributeError, TypeError, UnicodeError, ValueError):
            raise CredentialCryptoError("credential_unreadable") from None

    @staticmethod
    def _secret_value(value: SecretT, context: CredentialContext[SecretT]) -> str:
        if not isinstance(context.channel_public_id, UUID):
            raise CredentialCryptoError("encryption_failed")
        if context.kind == "access_token" and isinstance(value, AccessToken):
            plaintext = value.reveal_for_use()
        elif context.kind == "channel_secret" and isinstance(value, ChannelSecret):
            plaintext = value.reveal_for_use()
        else:
            raise CredentialCryptoError("encryption_failed")
        try:
            encoded = plaintext.encode("utf-8")
        except UnicodeEncodeError:
            raise CredentialCryptoError("encryption_failed") from None
        if not plaintext or len(encoded) > 16 * 1024:
            raise CredentialCryptoError("encryption_failed")
        return plaintext

    @staticmethod
    def _validate_envelope(
        envelope: object,
        context: CredentialContext[SecretT],
    ) -> str:
        if not isinstance(context.channel_public_id, UUID):
            raise ValueError
        if not isinstance(envelope, dict) or set(envelope) != {
            "format_version",
            "channel_public_id",
            "credential_kind",
            "value",
        }:
            raise ValueError
        if type(envelope["format_version"]) is not int or envelope["format_version"] != 1:
            raise ValueError
        if envelope["channel_public_id"] != str(context.channel_public_id):
            raise ValueError
        if context.kind not in ("access_token", "channel_secret"):
            raise ValueError
        if envelope["credential_kind"] != context.kind:
            raise ValueError
        plaintext = envelope["value"]
        if not isinstance(plaintext, str) or not plaintext:
            raise ValueError
        if len(plaintext.encode("utf-8")) > 16 * 1024:
            raise ValueError
        return plaintext


class ValidatedCredentialKeyring:
    __slots__ = ("__keys",)

    def __init__(self, keys: tuple[bytes, ...]) -> None:
        object.__setattr__(self, "_ValidatedCredentialKeyring__keys", keys)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("validated keyrings are immutable")

    def __repr__(self) -> str:
        return "<ValidatedCredentialKeyring redacted>"

    __str__ = __repr__

    def __reduce__(self) -> object:
        raise TypeError("serialization is disabled")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("serialization is disabled")

    def _same_material(self, other: "ValidatedCredentialKeyring") -> bool:
        return self.__keys == other.__keys


def parse_credential_keyring(raw: str | None) -> ValidatedCredentialKeyring:
    try:
        if not isinstance(raw, str) or not raw:
            raise ValueError
        if any(character.isspace() for character in raw) or '"' in raw or "'" in raw:
            raise ValueError
        raw.encode("ascii")
        encoded_keys = raw.split(",")
        if any(not key for key in encoded_keys):
            raise ValueError

        decoded_keys: list[bytes] = []
        for encoded_key in encoded_keys:
            encoded_bytes = encoded_key.encode("ascii")
            decoded_key = base64.b64decode(encoded_bytes, altchars=b"-_", validate=True)
            if len(decoded_key) != 32:
                raise ValueError
            if base64.urlsafe_b64encode(decoded_key) != encoded_bytes:
                raise ValueError
            if decoded_key in decoded_keys:
                raise ValueError
            decoded_keys.append(decoded_key)
    except (UnicodeEncodeError, ValueError, binascii.Error):
        raise CredentialKeyringConfigurationError("credential_keyring_invalid") from None

    return ValidatedCredentialKeyring(tuple(decoded_keys))
