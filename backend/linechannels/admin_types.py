from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
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


@dataclass(frozen=True, slots=True)
class LockedAdminChannel:
    public_id: UUID
    label: str
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


ExactChannelSnapshotFailureCode = Literal[
    "channel_unavailable",
    "channel_inactive",
    "stale_channel",
    "credential_unavailable",
    "credential_unreadable",
    "storage_retryable",
    "storage_unavailable",
]
ChannelSnapshotFailureCode = ExactChannelSnapshotFailureCode


@dataclass(frozen=True, repr=False)
class ChannelSnapshotCommand(_SerializationDisabled):
    """Rich-menu専用のprovider完全一致snapshot要求。"""

    channel_public_id: UUID
    owner_identity_public_id: UUID
    provider_id: str
    expected_channel_revision: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.channel_public_id, UUID):
            raise ValueError("invalid channel public id")
        if not isinstance(self.owner_identity_public_id, UUID):
            raise ValueError("invalid owner identity public id")
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("invalid provider id")
        if (
            not isinstance(self.expected_channel_revision, datetime)
            or self.expected_channel_revision.tzinfo is None
            or self.expected_channel_revision.utcoffset() is None
        ):
            raise ValueError("invalid channel revision")

    @property
    def owner_provider_id(self) -> str:
        return self.provider_id

    def __repr__(self) -> str:
        return (
            "<ChannelSnapshotCommand "
            f"channel_public_id={self.channel_public_id} "
            f"owner_identity_public_id={self.owner_identity_public_id} "
            f"provider_id={self.provider_id} expected_channel_revision="
            f"{self.expected_channel_revision.isoformat()}>"
        )

    __str__ = __repr__


class RichMenuChannelSnapshot(_SerializationDisabled):
    """LINE call scope. The access token cannot be serialized or displayed."""

    __slots__ = (
        "owner_identity_public_id",
        "provider_id",
        "channel_public_id",
        "channel_label",
        "is_active",
        "channel_revision",
        "__access_token",
    )

    def __init__(
        self,
        *,
        owner_identity_public_id: UUID,
        provider_id: str,
        channel_public_id: UUID,
        channel_label: str,
        is_active: bool,
        channel_revision: datetime,
        access_token: AccessToken,
    ) -> None:
        if not isinstance(owner_identity_public_id, UUID):
            raise ValueError("invalid owner identity public id")
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("invalid provider id")
        if not isinstance(channel_public_id, UUID):
            raise ValueError("invalid channel public id")
        if not isinstance(channel_label, str) or not channel_label:
            raise ValueError("invalid channel label")
        if type(is_active) is not bool:
            raise ValueError("invalid channel state")
        if (
            not isinstance(channel_revision, datetime)
            or channel_revision.tzinfo is None
            or channel_revision.utcoffset() is None
        ):
            raise ValueError("invalid channel revision")
        if not isinstance(access_token, AccessToken):
            raise ValueError("invalid access token")
        object.__setattr__(self, "owner_identity_public_id", owner_identity_public_id)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "channel_public_id", channel_public_id)
        object.__setattr__(self, "channel_label", channel_label)
        object.__setattr__(self, "is_active", is_active)
        object.__setattr__(self, "channel_revision", channel_revision)
        object.__setattr__(self, "_RichMenuChannelSnapshot__access_token", access_token)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("channel snapshots are immutable")

    @property
    def access_token(self) -> AccessToken:
        return self.__access_token

    @property
    def expected_channel_revision(self) -> datetime:
        return self.channel_revision

    @property
    def expected_updated_at(self) -> datetime:
        return self.channel_revision

    def __repr__(self) -> str:
        return (
            "<RichMenuChannelSnapshot "
            f"channel_public_id={self.channel_public_id} "
            f"owner_identity_public_id={self.owner_identity_public_id} "
            f"provider_id={self.provider_id} label=redacted active={self.is_active} "
            f"channel_revision={self.channel_revision.isoformat()} "
            "access_token=redacted>"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class ExactChannelSnapshotAvailable(_SerializationDisabled):
    snapshot: RichMenuChannelSnapshot
    status: Literal["available"] = "available"

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, RichMenuChannelSnapshot):
            raise ValueError("invalid rich menu channel snapshot")

    def __repr__(self) -> str:
        return f"<ExactChannelSnapshotAvailable snapshot={self.snapshot!r}>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class ExactChannelSnapshotRejected:
    code: ExactChannelSnapshotFailureCode
    status: Literal["rejected"] = "rejected"

    def __post_init__(self) -> None:
        if self.code not in {
            "channel_unavailable",
            "channel_inactive",
            "stale_channel",
            "credential_unavailable",
            "credential_unreadable",
            "storage_retryable",
            "storage_unavailable",
        }:
            raise ValueError("invalid exact channel snapshot rejection")


ExactChannelSnapshotResult = ExactChannelSnapshotAvailable | ExactChannelSnapshotRejected
ChannelSnapshotAvailable = ExactChannelSnapshotAvailable
ChannelSnapshotRejected = ExactChannelSnapshotRejected
ChannelSnapshotResult = ExactChannelSnapshotResult


class ChannelRevisionProof(_SerializationDisabled):
    __slots__ = (
        "owner_identity_public_id",
        "provider_id",
        "channel_public_id",
        "channel_revision",
    )

    def __init__(
        self,
        *,
        owner_identity_public_id: UUID,
        provider_id: str,
        channel_public_id: UUID,
        channel_revision: datetime,
    ) -> None:
        if not isinstance(owner_identity_public_id, UUID):
            raise ValueError("invalid owner identity public id")
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("invalid provider id")
        if not isinstance(channel_public_id, UUID):
            raise ValueError("invalid channel public id")
        if (
            not isinstance(channel_revision, datetime)
            or channel_revision.tzinfo is None
            or channel_revision.utcoffset() is None
        ):
            raise ValueError("invalid channel revision")
        object.__setattr__(self, "owner_identity_public_id", owner_identity_public_id)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "channel_public_id", channel_public_id)
        object.__setattr__(self, "channel_revision", channel_revision)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("channel revision proofs are immutable")

    @classmethod
    def from_snapshot(cls, snapshot: RichMenuChannelSnapshot) -> "ChannelRevisionProof":
        if not isinstance(snapshot, RichMenuChannelSnapshot):
            raise ValueError("invalid channel snapshot")
        return cls(
            owner_identity_public_id=snapshot.owner_identity_public_id,
            provider_id=snapshot.provider_id,
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
        )

    @property
    def expected_channel_revision(self) -> datetime:
        return self.channel_revision

    def __repr__(self) -> str:
        return (
            "<ChannelRevisionProof "
            f"channel_public_id={self.channel_public_id} "
            f"owner_identity_public_id={self.owner_identity_public_id} "
            f"provider_id={self.provider_id} "
            f"channel_revision={self.channel_revision.isoformat()}>"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class ChannelRevisionUnchanged:
    status: Literal["unchanged"] = "unchanged"


ChannelRevisionResult = ChannelRevisionUnchanged | ExactChannelSnapshotRejected


class OwnerChannelOperationPort(Protocol):
    def snapshot_exact(
        self, command: ChannelSnapshotCommand
    ) -> ChannelSnapshotResult: ...

    def lock_unchanged(
        self, proof: ChannelRevisionProof
    ) -> ChannelRevisionResult: ...


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


BotInfoFailureCode = Literal[
    "authentication_failed",
    "rate_limited",
    "line_unavailable",
]


@dataclass(frozen=True, slots=True)
class BotIdentityReceived:
    bot_user_id: str
    status: Literal["received"] = "received"


@dataclass(frozen=True, slots=True)
class BotInfoFailed:
    code: BotInfoFailureCode
    status: Literal["failed"] = "failed"


BotInfoResult = BotIdentityReceived | BotInfoFailed


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
