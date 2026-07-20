from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias
from uuid import UUID


FrozenJsonScalar: TypeAlias = str | int | float | bool | None
FrozenJsonValue: TypeAlias = (
    FrozenJsonScalar | tuple["FrozenJsonValue", ...] | "FrozenJsonObject"
)


def _freeze_json(value: object) -> FrozenJsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return FrozenJsonObject(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError("unsupported JSON value")


class FrozenJsonObject(Mapping[str, FrozenJsonValue]):
    __slots__ = ("__data",)

    def __init__(self, value: Mapping[str, object]) -> None:
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        object.__setattr__(
            self,
            "_FrozenJsonObject__data",
            {key: _freeze_json(item) for key, item in value.items()},
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("frozen JSON objects are immutable")

    def __getitem__(self, key: str) -> FrozenJsonValue:
        return self.__data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.__data)

    def __len__(self) -> int:
        return len(self.__data)

    def __repr__(self) -> str:
        return "<FrozenJsonObject redacted>"

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class VerifiedEventData:
    webhook_event_id: str
    event_type: str
    occurred_at_ms: int
    is_redelivery: bool
    event: FrozenJsonObject

    def __repr__(self) -> str:
        return (
            "<VerifiedEventData "
            f"webhook_event_id={self.webhook_event_id} "
            f"event_type={self.event_type} redelivery={self.is_redelivery}>"
        )


@dataclass(frozen=True, repr=False)
class VerifiedWebhookPayload:
    events: tuple[VerifiedEventData, ...]

    def __repr__(self) -> str:
        return f"<VerifiedWebhookPayload event_count={len(self.events)}>"


@dataclass(frozen=True, repr=False)
class VerifiedWebhookEvent:
    channel_public_id: UUID
    webhook_event_id: str
    event_type: str
    occurred_at_ms: int
    is_redelivery: bool
    data: FrozenJsonObject

    def __repr__(self) -> str:
        return (
            "<VerifiedWebhookEvent "
            f"channel_public_id={self.channel_public_id} "
            f"webhook_event_id={self.webhook_event_id} "
            f"event_type={self.event_type} redelivery={self.is_redelivery}>"
        )


@dataclass(frozen=True)
class PayloadRejected:
    status: Literal["rejected"] = "rejected"


@dataclass(frozen=True)
class HandlerSucceeded:
    status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True)
class HandlerFailed:
    status: Literal["failed"] = "failed"


HandlerOutcome: TypeAlias = HandlerSucceeded | HandlerFailed

IngressFailureCode: TypeAlias = Literal[
    "channel_unavailable",
    "signature_rejected",
    "payload_rejected",
    "storage_unavailable",
    "unexpected",
]


@dataclass(frozen=True)
class IngressAccepted:
    status: Literal["accepted"] = "accepted"


@dataclass(frozen=True)
class IngressRejected:
    code: IngressFailureCode
    status: Literal["rejected"] = "rejected"


IngressResult: TypeAlias = IngressAccepted | IngressRejected
ReceiptInitialStatus: TypeAlias = Literal["processing", "unsupported"]
ReceiptStatus: TypeAlias = Literal[
    "processing", "processed", "failed", "unsupported"
]


@dataclass(frozen=True)
class ReceiptCandidate:
    channel_public_id: UUID
    webhook_event_id: str
    event_type: str
    occurred_at_ms: int
    is_redelivery: bool
    initial_status: ReceiptInitialStatus


@dataclass(frozen=True)
class ReceiptDecision:
    receipt_id: int
    webhook_event_id: str
    status: ReceiptStatus
    created: bool


@dataclass(frozen=True)
class ReceiptStorageFailed:
    status: Literal["failed"] = "failed"


AuditOutcome: TypeAlias = Literal[
    "channel_rejected",
    "signature_rejected",
    "payload_rejected",
    "empty_accepted",
    "event_accepted",
    "event_duplicate",
    "event_unsupported",
    "handler_processed",
    "handler_failed",
    "storage_unavailable",
    "response_deadline_exceeded",
]


@dataclass(frozen=True)
class WebhookAuditEntry:
    outcome: AuditOutcome
    observed_at: datetime
    channel_public_id: UUID | None = None
    webhook_event_id: str | None = None
    event_type: str | None = None
    elapsed_ms: int | None = None

    def __post_init__(self) -> None:
        if self.outcome == "response_deadline_exceeded":
            if type(self.elapsed_ms) is not int or self.elapsed_ms < 0:
                raise ValueError("deadline audit requires non-negative elapsed_ms")
        elif self.elapsed_ms is not None:
            raise ValueError("elapsed_ms is only allowed for deadline audit")
