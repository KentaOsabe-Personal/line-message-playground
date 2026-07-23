from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal, Protocol, TypeAlias, runtime_checkable
from uuid import UUID

from lineaccounts.types import LineSubject
from linechannels.types import AccessToken
from linewebhooks.types import (
    HandlerExecutionContext,
    HandlerOutcome,
    VerifiedWebhookEvent,
)


class RedactedNonSerializableString:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("invalid redacted value")
        object.__setattr__(
            self,
            "_RedactedNonSerializableString__value",
            value,
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("redacted values are immutable")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"

    __str__ = __repr__

    def __reduce__(self) -> object:
        raise TypeError("serialization is disabled")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("serialization is disabled")

    def _reveal(self) -> str:
        return self.__value


class ReplyToken(RedactedNonSerializableString):
    __slots__ = ()

    def __init__(self, value: str) -> None:
        if not _valid_utf16_length(value, 1, 512):
            raise ValueError("invalid reply token")
        super().__init__(value)

    def reveal_for_reply(self) -> str:
        return self._reveal()


class OpaqueActionPayload(RedactedNonSerializableString):
    __slots__ = ()

    def reveal_for_action(self) -> str:
        return self._reveal()


@dataclass(frozen=True, slots=True, repr=False)
class ParsedTextInteraction:
    subject: LineSubject
    reply_token: ReplyToken
    candidate: str


@dataclass(frozen=True, slots=True, repr=False)
class ParsedPostbackInteraction:
    subject: LineSubject
    reply_token: ReplyToken
    action_name: str
    payload: OpaqueActionPayload


@dataclass(frozen=True, slots=True)
class InvalidInteraction:
    classification: Literal["invalid"] = "invalid"


@dataclass(frozen=True, slots=True)
class OutOfScopeInteraction:
    classification: Literal["out_of_scope"] = "out_of_scope"


ParseResult: TypeAlias = (
    ParsedTextInteraction
    | ParsedPostbackInteraction
    | InvalidInteraction
    | OutOfScopeInteraction
)


@dataclass(frozen=True, slots=True)
class VerifiedInteractionChannel:
    channel_public_id: UUID
    provider_id: str


@dataclass(frozen=True, slots=True)
class VerifiedInteractionUser:
    identity_public_id: UUID
    recipient_public_id: UUID


@dataclass(frozen=True, slots=True)
class LinkedInteractionUserMissing:
    status: Literal["missing"] = "missing"


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    identifier: Literal["connectivity_ping_v1"]
    exact_text: Literal["/ping"]
    reply_text: Literal["pong"]


@dataclass(frozen=True, slots=True)
class ActionSucceeded:
    status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True, slots=True)
class ActionNoChange:
    status: Literal["no_change"] = "no_change"


@dataclass(frozen=True, slots=True)
class ActionRejected:
    status: Literal["rejected"] = "rejected"


@dataclass(frozen=True, slots=True)
class ActionFailed:
    status: Literal["failed"] = "failed"


ActionOutcome: TypeAlias = (
    ActionSucceeded | ActionNoChange | ActionRejected | ActionFailed
)


@dataclass(frozen=True, slots=True, repr=False)
class PostbackActionCommand:
    action_name: str
    payload: OpaqueActionPayload
    channel: VerifiedInteractionChannel
    webhook_event_id: str
    user: VerifiedInteractionUser
    execution: HandlerExecutionContext


@dataclass(frozen=True, slots=True)
class ReplyAccepted:
    status: Literal["accepted"] = "accepted"


@dataclass(frozen=True, slots=True)
class ReplyRejected:
    status: Literal["rejected"] = "rejected"


@dataclass(frozen=True, slots=True)
class ReplyUnknown:
    status: Literal["unknown"] = "unknown"


ReplyResult: TypeAlias = ReplyAccepted | ReplyRejected | ReplyUnknown


@dataclass(frozen=True, slots=True)
class ReplyTimeoutBudget:
    total_seconds: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.total_seconds, (int, float))
            or isinstance(self.total_seconds, bool)
            or not isfinite(self.total_seconds)
            or self.total_seconds <= 0
        ):
            raise ValueError("invalid reply timeout budget")


InteractionOutcome: TypeAlias = Literal[
    "command_processed",
    "action_succeeded",
    "action_no_change",
    "action_rejected",
    "unknown",
    "invalid",
    "out_of_scope",
    "unlinked",
    "handler_failed",
    "processing_failed",
    "credential_unavailable",
    "deadline_exceeded",
]
ReplyOutcome: TypeAlias = Literal[
    "accepted", "rejected", "unknown", "not_started"
]
EventType: TypeAlias = Literal["message", "postback"]
OperationKind: TypeAlias = Literal["none", "command", "action"]


@dataclass(frozen=True, slots=True)
class InteractionAuditRecord:
    channel_public_id: UUID
    webhook_event_id: str
    event_type: EventType
    operation_kind: OperationKind
    operation_identifier: str | None
    interaction_outcome: InteractionOutcome
    reply_outcome: ReplyOutcome

    def __post_init__(self) -> None:
        if not _valid_audit_combination(self):
            raise ValueError("invalid interaction audit result")


@runtime_checkable
class InteractionParser(Protocol):
    def parse(self, event: VerifiedWebhookEvent) -> ParseResult: ...


@runtime_checkable
class InteractionAccountDirectory(Protocol):
    def resolve_linked(
        self,
        *,
        channel_public_id: UUID,
        provider_id: str,
        subject: LineSubject,
    ) -> VerifiedInteractionUser | LinkedInteractionUserMissing: ...


@runtime_checkable
class CommandRegistry(Protocol):
    def resolve(self, candidate: str) -> CommandDefinition | None: ...


@runtime_checkable
class PostbackActionHandler(Protocol):
    def handle(self, command: PostbackActionCommand) -> ActionOutcome: ...


@runtime_checkable
class PostbackActionRegistry(Protocol):
    def resolve(self, action_name: str) -> PostbackActionHandler | None: ...


@runtime_checkable
class LineReplyGateway(Protocol):
    def reply_text(
        self,
        *,
        access_token: AccessToken,
        reply_token: ReplyToken,
        text: str,
        timeout: ReplyTimeoutBudget,
    ) -> ReplyResult: ...


@runtime_checkable
class InteractionAuditRepository(Protocol):
    def record(
        self,
        audit: InteractionAuditRecord,
    ) -> Literal["recorded", "failed"]: ...


InteractionHandlerOutcome: TypeAlias = HandlerOutcome


def _valid_utf16_length(value: object, minimum: int, maximum: int) -> bool:
    if not isinstance(value, str):
        return False
    try:
        code_units = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        return False
    return minimum <= code_units <= maximum


def _valid_audit_combination(record: InteractionAuditRecord) -> bool:
    if (
        record.operation_kind == "none"
        and record.operation_identifier is None
        and record.reply_outcome == "not_started"
    ):
        return record.event_type in ("message", "postback") and (
            record.interaction_outcome
            in {
                "unknown",
                "invalid",
                "out_of_scope",
                "unlinked",
                "processing_failed",
            }
        )
    if (
        record.event_type == "message"
        and record.operation_kind == "command"
        and record.operation_identifier == "connectivity_ping_v1"
    ):
        if record.interaction_outcome == "command_processed":
            return record.reply_outcome in {
                "accepted",
                "rejected",
                "unknown",
            }
        return (
            record.interaction_outcome
            in {
                "processing_failed",
                "credential_unavailable",
                "deadline_exceeded",
            }
            and record.reply_outcome == "not_started"
        )
    return (
        record.event_type == "postback"
        and record.operation_kind == "action"
        and isinstance(record.operation_identifier, str)
        and bool(record.operation_identifier)
        and record.interaction_outcome
        in {
            "action_succeeded",
            "action_no_change",
            "action_rejected",
            "handler_failed",
        }
        and record.reply_outcome == "not_started"
    )
