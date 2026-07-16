import hashlib
import re
from dataclasses import dataclass, field
from hmac import compare_digest
from os import environ
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured

from linechannels.repositories import LineChannelDirectory
from linechannels.validators import BoundaryValidationError, validate_provider_id


_OPAQUE_NUMERIC_ID = re.compile(r"[0-9]{1,64}\Z", re.ASCII)
_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_KNOWN_DJANGO_SECRET = "local-development-secret-key"
_RUNTIME_ERROR = "LINE_ACCOUNT_RUNTIME_INVALID"


@dataclass(frozen=True, slots=True, repr=False)
class SecretValue:
    _value: str

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class OwnerEligibilityDigest:
    _value: str

    def matches(self, candidate: str) -> bool:
        return compare_digest(self._value, candidate)

    def __repr__(self) -> str:
        return "OwnerEligibilityDigest(<redacted>)"


@dataclass(frozen=True, slots=True)
class OwnerEligibilityUnavailable:
    reason: str = "not_configured"


OwnerEligibility = OwnerEligibilityDigest | OwnerEligibilityUnavailable


@dataclass(frozen=True, slots=True)
class LineAccountRuntime:
    channel_id: str
    channel_secret: SecretValue = field(repr=False)
    provider_id: str
    linked_channel_public_id: UUID
    owner_eligibility: OwnerEligibility = field(repr=False)


_runtime: LineAccountRuntime | None = None


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key)
    if value is None or not value or value != value.strip():
        raise ImproperlyConfigured(_RUNTIME_ERROR)
    return value


def _numeric_id(environment: Mapping[str, str], key: str) -> str:
    value = _required(environment, key)
    if not _OPAQUE_NUMERIC_ID.fullmatch(value):
        raise ImproperlyConfigured(_RUNTIME_ERROR)
    return value


def _provider_id(environment: Mapping[str, str], key: str) -> str:
    value = _required(environment, key)
    try:
        return validate_provider_id(value)
    except BoundaryValidationError:
        raise ImproperlyConfigured(_RUNTIME_ERROR) from None


def _channel_secret(environment: Mapping[str, str]) -> SecretValue:
    value = _required(environment, "LINE_LOGIN_CHANNEL_SECRET")
    if len(value) > 256 or not value.isascii() or not value.isprintable():
        raise ImproperlyConfigured(_RUNTIME_ERROR)
    return SecretValue(value)


def _linked_channel_id(environment: Mapping[str, str]) -> UUID:
    value = _required(environment, "LINE_LIFF_LINKED_CHANNEL_PUBLIC_ID")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ImproperlyConfigured(_RUNTIME_ERROR) from error
    if str(parsed) != value:
        raise ImproperlyConfigured(_RUNTIME_ERROR)
    return parsed


def _owner_eligibility(environment: Mapping[str, str]) -> OwnerEligibility:
    value = environment.get("LINE_OWNER_SUBJECT_DIGEST")
    if value is None or value == "":
        return OwnerEligibilityUnavailable()
    if value != value.strip() or not _LOWERCASE_SHA256.fullmatch(value):
        raise ImproperlyConfigured(_RUNTIME_ERROR)
    return OwnerEligibilityDigest(value)


def load_line_account_runtime(
    environment: Mapping[str, str],
) -> LineAccountRuntime:
    runtime_keys = (
        "LINE_LOGIN_CHANNEL_ID",
        "LINE_LOGIN_CHANNEL_SECRET",
        "LINE_LOGIN_PROVIDER_ID",
        "LINE_LIFF_LINKED_CHANNEL_PUBLIC_ID",
        "LINE_OWNER_SUBJECT_DIGEST",
    )
    safe_environment = MappingProxyType(
        {key: environment[key] for key in runtime_keys if key in environment}
    )
    return LineAccountRuntime(
        channel_id=_numeric_id(safe_environment, "LINE_LOGIN_CHANNEL_ID"),
        channel_secret=_channel_secret(safe_environment),
        provider_id=_provider_id(safe_environment, "LINE_LOGIN_PROVIDER_ID"),
        linked_channel_public_id=_linked_channel_id(safe_environment),
        owner_eligibility=_owner_eligibility(safe_environment),
    )


def validate_django_secret(secret: str) -> str:
    if (
        not secret
        or len(secret) < 32
        or secret == _KNOWN_DJANGO_SECRET
        or secret != secret.strip()
    ):
        raise ImproperlyConfigured("DJANGO_SECRET_KEY_INVALID")
    return secret


def initialize_line_account_runtime(django_secret: str) -> LineAccountRuntime:
    global _runtime
    validate_django_secret(django_secret)
    if _runtime is None:
        _runtime = load_line_account_runtime(environ)
    return _runtime


def get_line_account_runtime() -> LineAccountRuntime:
    if _runtime is None:
        raise ImproperlyConfigured("LINE_ACCOUNT_RUNTIME_NOT_INITIALIZED")
    return _runtime


def derive_owner_digest(provider_id: str, subject: str) -> str:
    try:
        validate_provider_id(provider_id)
    except BoundaryValidationError:
        raise ImproperlyConfigured("OWNER_DIGEST_INPUT_INVALID") from None
    if not subject or "\0" in subject:
        raise ImproperlyConfigured("OWNER_DIGEST_INPUT_INVALID")
    return hashlib.sha256(f"{provider_id}\0{subject}".encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LiffLinkedChannelPolicy:
    public_id: UUID

    def is_direct(self, channel_public_id: UUID) -> bool:
        return self.public_id == channel_public_id


def resolve_liff_linked_channel_policy(
    runtime: LineAccountRuntime,
    directory: LineChannelDirectory,
) -> LiffLinkedChannelPolicy:
    channel = directory.get(runtime.linked_channel_public_id)
    if channel is None or channel.provider_id != runtime.provider_id:
        raise ImproperlyConfigured("LINE_ACCOUNT_CHANNEL_POLICY_INVALID")
    return LiffLinkedChannelPolicy(channel.public_id)
