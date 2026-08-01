from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from .types import AccessToken, CredentialPair


class _SerializationDisabled:
    __slots__ = ()

    def __reduce__(self) -> object:
        raise TypeError("serialization is disabled")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("serialization is disabled")


@dataclass(frozen=True, repr=False)
class RegisterAdminChannel(_SerializationDisabled):
    messaging_api_channel_id: str
    bot_user_id: str
    label: str
    provider_id: str
    credentials: CredentialPair
    is_active: bool

    def __repr__(self) -> str:
        return (
            "<RegisterAdminChannel "
            f"channel_id={self.messaging_api_channel_id} "
            f"bot_user_id={self.bot_user_id} provider_id={self.provider_id} "
            f"active={self.is_active} credentials=redacted>"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class UpdateAdminChannel(_SerializationDisabled):
    channel_public_id: UUID
    expected_updated_at: datetime
    messaging_api_channel_id: str | None = None
    bot_user_id: str | None = None
    label: str | None = None
    provider_id: str | None = None
    credentials: CredentialPair | None = None

    def __repr__(self) -> str:
        fields = (
            "messaging_api_channel_id",
            "bot_user_id",
            "label",
            "provider_id",
        )
        specified = ", ".join(
            name for name in fields if getattr(self, name) is not None
        )
        return (
            f"<UpdateAdminChannel public_id={self.channel_public_id} "
            f"fields=[{specified}] credentials={self.credentials is not None}>"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class SetAdminChannelState(_SerializationDisabled):
    channel_public_id: UUID
    expected_updated_at: datetime
    is_active: bool
    repair_credentials: CredentialPair | None = None

    def __repr__(self) -> str:
        return (
            f"<SetAdminChannelState public_id={self.channel_public_id} "
            f"active={self.is_active} "
            f"repair_credentials={self.repair_credentials is not None}>"
        )

    __str__ = __repr__


@dataclass(frozen=True)
class DeleteAdminChannel:
    channel_public_id: UUID
    expected_updated_at: datetime


CredentialsState = Literal["configured", "repair_required"]


@dataclass(frozen=True, slots=True)
class AdminChannelView:
    public_id: UUID
    messaging_api_channel_id: str
    bot_user_id: str
    label: str
    provider_id: str | None
    is_active: bool
    credentials_state: CredentialsState
    credentials_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminConnectionSnapshot(_SerializationDisabled):
    __slots__ = (
        "__access_token",
        "expected_bot_user_id",
        "expected_updated_at",
    )

    def __init__(
        self,
        *,
        access_token: AccessToken,
        expected_bot_user_id: str,
        expected_updated_at: datetime,
    ) -> None:
        object.__setattr__(self, "_AdminConnectionSnapshot__access_token", access_token)
        object.__setattr__(self, "expected_bot_user_id", expected_bot_user_id)
        object.__setattr__(self, "expected_updated_at", expected_updated_at)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("connection snapshots are immutable")

    @property
    def access_token(self) -> AccessToken:
        return self.__access_token

    def __repr__(self) -> str:
        return (
            "<AdminConnectionSnapshot access_token=redacted "
            f"expected_bot_user_id={self.expected_bot_user_id} "
            f"expected_updated_at={self.expected_updated_at.isoformat()}>"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class SnapshotAvailable(_SerializationDisabled):
    snapshot: AdminConnectionSnapshot
    status: Literal["available"] = "available"

    @property
    def access_token(self) -> AccessToken:
        return self.snapshot.access_token

    @property
    def expected_bot_user_id(self) -> str:
        return self.snapshot.expected_bot_user_id

    @property
    def expected_updated_at(self) -> datetime:
        return self.snapshot.expected_updated_at

    def __repr__(self) -> str:
        return f"<SnapshotAvailable snapshot={self.snapshot!r}>"

    __str__ = __repr__


AdminRepositoryFailureCode = Literal[
    "credential_unavailable",
    "credential_unreadable",
    "channel_not_found",
    "stale_channel",
    "storage_retryable",
    "storage_unavailable",
]


@dataclass(frozen=True)
class AdminRepositoryUnavailable:
    code: AdminRepositoryFailureCode
    status: Literal["unavailable"] = "unavailable"


@dataclass(frozen=True)
class AdminRepositoryFailed:
    code: AdminRepositoryFailureCode
    status: Literal["failed"] = "failed"


@dataclass(frozen=True)
class ConnectionRevisionUnchanged:
    status: Literal["unchanged"] = "unchanged"


AdminConnectionSnapshotResult = (
    SnapshotAvailable | AdminRepositoryUnavailable | AdminRepositoryFailed
)
ConnectionRevisionResult = ConnectionRevisionUnchanged | AdminRepositoryFailed


AdminServiceFailureCode = Literal[
    "authentication_required",
    "owner_operation_blocked",
    "invalid_input",
    "duplicate_channel",
    "channel_not_found",
    "stale_channel",
    "provider_mismatch",
    "provider_immutable",
    "credential_unavailable",
    "encryption_failed",
    "credential_unreadable",
    "channel_referenced",
    "storage_retryable",
    "storage_unavailable",
]


@dataclass(frozen=True, slots=True)
class AdminServiceFailed:
    code: AdminServiceFailureCode
    status: Literal["failed"] = "failed"


@dataclass(frozen=True, slots=True)
class ChannelListSucceeded:
    channels: tuple[AdminChannelView, ...]
    status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True, slots=True)
class ChannelReadSucceeded:
    channel: AdminChannelView
    status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True, slots=True)
class AdminChannelMutationSucceeded:
    channel: AdminChannelView
    status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True, slots=True)
class ChannelDeleteSucceeded:
    channel_public_id: UUID
    label: str
    status: Literal["succeeded"] = "succeeded"


ConnectionStatus = Literal[
    "connected",
    "credential_unavailable",
    "authentication_failed",
    "identity_mismatch",
    "rate_limited",
    "line_unavailable",
]
CONNECTION_CHECK_SCOPE = "access_token_and_bot_identity_only"


@dataclass(frozen=True, slots=True)
class ConnectionCheckCompleted:
    status: ConnectionStatus
    checked_at: datetime
    scope: Literal["access_token_and_bot_identity_only"] = CONNECTION_CHECK_SCOPE

    def __post_init__(self) -> None:
        allowed = {
            "connected",
            "credential_unavailable",
            "authentication_failed",
            "identity_mismatch",
            "rate_limited",
            "line_unavailable",
        }
        if (
            self.status not in allowed
            or self.checked_at.tzinfo is None
            or self.checked_at.utcoffset() is None
            or self.scope != CONNECTION_CHECK_SCOPE
        ):
            raise ValueError("invalid connection check result")


ChannelListResult = ChannelListSucceeded | AdminServiceFailed
ChannelReadResult = ChannelReadSucceeded | AdminServiceFailed
ChannelMutationResult = AdminChannelMutationSucceeded | AdminServiceFailed
ChannelDeleteResult = ChannelDeleteSucceeded | AdminServiceFailed
ConnectionCheckResult = ConnectionCheckCompleted | AdminServiceFailed
