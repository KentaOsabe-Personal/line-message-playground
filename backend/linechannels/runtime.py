import os
from threading import RLock

from .crypto import (
    CredentialKeyringConfigurationError,
    ValidatedCredentialKeyring,
    parse_credential_keyring,
)


_state_lock = RLock()
_validated_keyring: ValidatedCredentialKeyring | None = None


def load_credential_keyring() -> None:
    candidate = parse_credential_keyring(os.environ.get("LINE_CHANNEL_CREDENTIAL_KEYS"))
    global _validated_keyring
    with _state_lock:
        if _validated_keyring is None:
            _validated_keyring = candidate
            return
        if not _validated_keyring._same_material(candidate):
            raise CredentialKeyringConfigurationError(
                "credential_keyring_already_initialized"
            )


def get_validated_keyring() -> ValidatedCredentialKeyring:
    with _state_lock:
        if _validated_keyring is None:
            raise CredentialKeyringConfigurationError(
                "credential_keyring_not_initialized"
            )
        return _validated_keyring


def _reset_runtime_state_for_tests() -> None:
    global _validated_keyring
    with _state_lock:
        _validated_keyring = None
