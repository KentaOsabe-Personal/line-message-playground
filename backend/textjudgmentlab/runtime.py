import re
from dataclasses import dataclass, field
from hmac import compare_digest
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

_OPAQUE_NUMERIC_ID = re.compile(r"[0-9]{1,64}\Z", re.ASCII)
_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MODEL = "jev-1.13.0"


class SecretValue:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        object.__setattr__(self, "_SecretValue__value", value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("secret values are immutable")

    def reveal_for_remote_call(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"


class OwnerDigest:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        object.__setattr__(self, "_OwnerDigest__value", value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("digest values are immutable")

    def matches(self, candidate: str) -> bool:
        return compare_digest(self.__value, candidate)

    def __repr__(self) -> str:
        return "OwnerDigest(<redacted>)"


@dataclass(frozen=True, slots=True)
class LabRuntimeDisabled:
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class LabRuntimeUnavailable:
    reason: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class LabRuntimeConfigured:
    channel_id: str
    owner_digest: OwnerDigest = field(repr=False)
    origin: str
    api_key: SecretValue = field(repr=False)
    model: str
    enabled: bool = True


LabRuntime = LabRuntimeDisabled | LabRuntimeUnavailable | LabRuntimeConfigured


def _required(environment: Mapping[str, str], key: str) -> str:
    value = environment.get(key)
    if value is None or not value or value != value.strip():
        raise ValueError("invalid configuration")
    return value


def _canonical_https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.isascii()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path != ""
        or parsed.query
        or parsed.fragment
        or value != f"https://{parsed.hostname}"
    ):
        raise ValueError("invalid configuration")
    return value


def _load_configured(environment: Mapping[str, str]) -> LabRuntimeConfigured:
    channel_id = _required(environment, "TEXT_JUDGMENT_LAB_CHANNEL_ID")
    owner_digest = _required(environment, "TEXT_JUDGMENT_LAB_OWNER_DIGEST")
    api_key = _required(environment, "TYPESAFE_API_KEY")
    model = _required(environment, "TEXT_JUDGMENT_LAB_MODEL")
    if not _OPAQUE_NUMERIC_ID.fullmatch(channel_id):
        raise ValueError("invalid configuration")
    if not _LOWERCASE_SHA256.fullmatch(owner_digest):
        raise ValueError("invalid configuration")
    if len(api_key) > 8 * 1024 or "\0" in api_key:
        raise ValueError("invalid configuration")
    if model != _MODEL:
        raise ValueError("invalid configuration")
    return LabRuntimeConfigured(
        channel_id=channel_id,
        owner_digest=OwnerDigest(owner_digest),
        origin=_canonical_https_origin(_required(environment, "TEXT_JUDGMENT_LAB_ORIGIN")),
        api_key=SecretValue(api_key),
        model=model,
    )


def load_lab_runtime(environment: Mapping[str, str], *, debug: bool) -> LabRuntime:
    keys = (
        "TEXT_JUDGMENT_LAB_ENABLED",
        "TEXT_JUDGMENT_LAB_CHANNEL_ID",
        "TEXT_JUDGMENT_LAB_OWNER_DIGEST",
        "TEXT_JUDGMENT_LAB_ORIGIN",
        "TYPESAFE_API_KEY",
        "TEXT_JUDGMENT_LAB_MODEL",
    )
    safe_environment = MappingProxyType(
        {key: environment[key] for key in keys if key in environment}
    )
    enabled = safe_environment.get("TEXT_JUDGMENT_LAB_ENABLED", "false")
    if enabled == "false":
        return LabRuntimeDisabled()
    if enabled != "true":
        return LabRuntimeUnavailable("invalid_configuration")
    if debug:
        return LabRuntimeUnavailable("debug_enabled")
    try:
        return _load_configured(safe_environment)
    except TypeError, ValueError:
        return LabRuntimeUnavailable("invalid_configuration")
