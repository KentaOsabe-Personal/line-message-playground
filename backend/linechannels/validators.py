import re
from uuid import UUID

from .types import AccessToken, ChannelSecret, CredentialPair


_CHANNEL_ID_PATTERN = re.compile(r"^[0-9]{1,64}$")
_BOT_USER_ID_PATTERN = re.compile(r"^U[0-9a-f]{32}$")
_MAX_LABEL_LENGTH = 255
_MAX_SECRET_BYTES = 16 * 1024


class BoundaryValidationError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid_input")


def validate_messaging_api_channel_id(value: str) -> str:
    if not isinstance(value, str) or _CHANNEL_ID_PATTERN.fullmatch(value) is None:
        raise BoundaryValidationError()
    return value


def validate_bot_user_id(value: str) -> str:
    if not isinstance(value, str) or _BOT_USER_ID_PATTERN.fullmatch(value) is None:
        raise BoundaryValidationError()
    return value


def validate_label(value: str) -> str:
    if not isinstance(value, str):
        raise BoundaryValidationError()
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_LABEL_LENGTH:
        raise BoundaryValidationError()
    return normalized


def validate_public_id(value: str | UUID) -> UUID:
    try:
        public_id = value if isinstance(value, UUID) else UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise BoundaryValidationError() from None
    if public_id.version != 4:
        raise BoundaryValidationError()
    return public_id


def _validate_secret(value: str | None) -> str:
    if not isinstance(value, str) or not value or value.isspace():
        raise BoundaryValidationError()
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise BoundaryValidationError() from None
    if len(encoded) > _MAX_SECRET_BYTES:
        raise BoundaryValidationError()
    return value


def build_credential_pair(
    access_token: str | None,
    channel_secret: str | None,
) -> CredentialPair:
    token = _validate_secret(access_token)
    secret = _validate_secret(channel_secret)
    return CredentialPair(AccessToken(token), ChannelSecret(secret))


def validate_credential_pair(value: CredentialPair) -> CredentialPair:
    if (
        not isinstance(value, CredentialPair)
        or not isinstance(value.access_token, AccessToken)
        or not isinstance(value.channel_secret, ChannelSecret)
    ):
        raise BoundaryValidationError()
    return build_credential_pair(
        value.access_token.reveal_for_use(),
        value.channel_secret.reveal_for_use(),
    )
