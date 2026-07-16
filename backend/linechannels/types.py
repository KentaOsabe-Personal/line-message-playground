from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID

class _SerializationDisabled:
    __slots__ = ()

    def __reduce__(self) -> object:
        raise TypeError("serialization is disabled")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("serialization is disabled")


class _RedactedValue(_SerializationDisabled):
    __slots__ = ("__value",)

    def __init__(self, value: str | bytes) -> None:
        object.__setattr__(self, "_RedactedValue__value", value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("redacted values are immutable")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"

    __str__ = __repr__

    def _reveal(self) -> str | bytes:
        return self.__value


class AccessToken(_RedactedValue):
    __slots__ = ()

    def __init__(self, value: str) -> None:
        super().__init__(value)

    def reveal_for_use(self) -> str:
        return self._reveal()  # type: ignore[return-value]


class ChannelSecret(_RedactedValue):
    __slots__ = ()

    def __init__(self, value: str) -> None:
        super().__init__(value)

    def reveal_for_use(self) -> str:
        return self._reveal()  # type: ignore[return-value]


class EncryptedCredential(_RedactedValue):
    __slots__ = ()

    def __init__(self, ciphertext: bytes) -> None:
        super().__init__(ciphertext)

    @property
    def ciphertext(self) -> bytes:
        return self._reveal()  # type: ignore[return-value]


class CredentialPair(_SerializationDisabled):
    __slots__ = ("__access_token", "__channel_secret")

    def __init__(self, access_token: AccessToken, channel_secret: ChannelSecret) -> None:
        object.__setattr__(self, "_CredentialPair__access_token", access_token)
        object.__setattr__(self, "_CredentialPair__channel_secret", channel_secret)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("credential pairs are immutable")

    @property
    def access_token(self) -> AccessToken:
        return self.__access_token

    @property
    def channel_secret(self) -> ChannelSecret:
        return self.__channel_secret

    def __repr__(self) -> str:
        return "<CredentialPair redacted>"

    __str__ = __repr__


class EncryptedCredentialPair(_SerializationDisabled):
    __slots__ = ("__access_token", "__channel_secret")

    def __init__(
        self,
        access_token: EncryptedCredential,
        channel_secret: EncryptedCredential,
    ) -> None:
        object.__setattr__(self, "_EncryptedCredentialPair__access_token", access_token)
        object.__setattr__(self, "_EncryptedCredentialPair__channel_secret", channel_secret)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("encrypted credential pairs are immutable")

    @property
    def access_token(self) -> EncryptedCredential:
        return self.__access_token

    @property
    def channel_secret(self) -> EncryptedCredential:
        return self.__channel_secret

    def __repr__(self) -> str:
        return "<EncryptedCredentialPair redacted>"

    __str__ = __repr__


@dataclass(frozen=True)
class PublicChannelSummary:
    public_id: UUID
    messaging_api_channel_id: str
    bot_user_id: str
    label: str
    is_active: bool
    credentials_configured: bool
    created_at: datetime
    updated_at: datetime
    provider_id: str | None = None


@dataclass(frozen=True)
class LinkableChannelSummary:
    public_id: UUID
    label: str
    provider_id: str
    is_active: bool


MutationFailureCode = Literal[
    "duplicate_channel",
    "channel_not_found",
    "invalid_input",
    "invalid_transition",
    "encryption_failed",
    "credential_unreadable",
    "retryable",
    "storage_unavailable",
]


@dataclass(frozen=True)
class ChannelMutationSucceeded:
    channel: PublicChannelSummary
    status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True)
class ChannelMutationFailed:
    code: MutationFailureCode
    status: Literal["failed"] = "failed"


@dataclass(frozen=True, repr=False)
class RegisterLineChannel:
    messaging_api_channel_id: str
    bot_user_id: str
    label: str
    credentials: CredentialPair
    is_active: bool
    provider_id: str | None = None

    def __repr__(self) -> str:
        return (
            "<RegisterLineChannel "
            f"channel_id={self.messaging_api_channel_id} "
            f"bot_user_id={self.bot_user_id} provider_id={self.provider_id} "
            f"active={self.is_active} "
            "credentials=redacted>"
        )


@dataclass(frozen=True, repr=False)
class UpdateLineChannel:
    channel_public_id: UUID
    messaging_api_channel_id: str | None = None
    bot_user_id: str | None = None
    label: str | None = None
    credentials: CredentialPair | None = None
    is_active: bool | None = None
    provider_id: str | None = None

    def __repr__(self) -> str:
        fields = (
            "messaging_api_channel_id",
            "bot_user_id",
            "label",
            "is_active",
            "provider_id",
        )
        specified = ", ".join(
            field for field in fields if getattr(self, field) is not None
        )
        return (
            f"<UpdateLineChannel public_id={self.channel_public_id} "
            f"fields=[{specified}] credentials={self.credentials is not None}>"
        )


@dataclass(frozen=True)
class SetLineChannelActive:
    channel_public_id: UUID
    active: bool


ManageLineChannelInput = (
    RegisterLineChannel | UpdateLineChannel | SetLineChannelActive
)


@dataclass(frozen=True, repr=False)
class ManageLineChannelInputCollected:
    value: ManageLineChannelInput
    status: Literal["collected"] = "collected"

    def __repr__(self) -> str:
        return f"<ManageLineChannelInputCollected value={self.value!r}>"


@dataclass(frozen=True)
class ManageLineChannelInputCancelled:
    status: Literal["cancelled"] = "cancelled"


@dataclass(frozen=True)
class ManageLineChannelInputInvalid:
    status: Literal["invalid"] = "invalid"


ManageLineChannelInputResult = (
    ManageLineChannelInputCollected
    | ManageLineChannelInputCancelled
    | ManageLineChannelInputInvalid
)


ChannelMutationResult = ChannelMutationSucceeded | ChannelMutationFailed


SecretT = TypeVar("SecretT", AccessToken, ChannelSecret)


@dataclass(frozen=True)
class CredentialContext(Generic[SecretT]):
    channel_public_id: UUID
    kind: Literal["access_token", "channel_secret"]


@dataclass(frozen=True)
class CredentialAvailable(Generic[SecretT]):
    value: SecretT
    status: Literal["available"] = "available"


CredentialFailureCode = Literal[
    "channel_not_found",
    "channel_inactive",
    "credentials_incomplete",
    "credential_unreadable",
]


@dataclass(frozen=True)
class CredentialUnavailable:
    code: CredentialFailureCode
    status: Literal["unavailable"] = "unavailable"


RotationReadiness = Literal["ready", "old_key_missing"]
