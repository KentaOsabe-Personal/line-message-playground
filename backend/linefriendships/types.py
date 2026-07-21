from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, TypeAlias, runtime_checkable
from uuid import UUID

from lineaccounts.types import LineSubject
from linewebhooks.handlers import VerifiedEventHandler
from linewebhooks.types import HandlerOutcome, VerifiedWebhookEvent


FriendshipState: TypeAlias = Literal["friend", "not_friend"]
StoredFriendshipState: TypeAlias = Literal["friend", "not_friend", "unknown"]
FriendshipEventType: TypeAlias = Literal["follow", "unfollow"]
ProjectionOutcome: TypeAlias = Literal[
    "applied",
    "state_maintained",
    "stale",
    "duplicate",
    "unlinked",
    "unresolvable",
    "out_of_scope",
    "invalid",
]

_EVENT_TYPES = frozenset(("follow", "unfollow"))
_FRIENDSHIP_STATES = frozenset(("friend", "not_friend"))
_STORED_FRIENDSHIP_STATES = frozenset(("friend", "not_friend", "unknown"))
_PROJECTION_OUTCOMES = frozenset(
    (
        "applied",
        "state_maintained",
        "stale",
        "duplicate",
        "unlinked",
        "unresolvable",
        "out_of_scope",
        "invalid",
    )
)


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedFriendshipEvent:
    channel_public_id: UUID
    webhook_event_id: str
    event_type: FriendshipEventType
    occurred_at_ms: int
    subject: LineSubject
    target_state: FriendshipState
    is_unblocked: bool | None

    def __post_init__(self) -> None:
        if self.event_type not in _EVENT_TYPES:
            raise ValueError("invalid friendship event type")
        if type(self.occurred_at_ms) is not int or self.occurred_at_ms < 0:
            raise ValueError("invalid friendship event timestamp")
        if not isinstance(self.subject, LineSubject):
            raise ValueError("invalid friendship subject")
        if self.target_state not in _FRIENDSHIP_STATES:
            raise ValueError("invalid friendship target state")
        if self.is_unblocked is not None and type(self.is_unblocked) is not bool:
            raise ValueError("invalid unblock flag")
        if self.event_type == "unfollow" and self.is_unblocked is not None:
            raise ValueError("unfollow cannot carry unblock flag")

    def __repr__(self) -> str:
        return (
            "<ValidatedFriendshipEvent "
            f"channel_public_id={self.channel_public_id} "
            f"webhook_event_id={self.webhook_event_id} "
            f"event_type={self.event_type}>"
        )


@dataclass(frozen=True, slots=True)
class InvalidFriendshipEvent:
    classification: Literal["invalid"] = "invalid"


@dataclass(frozen=True, slots=True)
class OutOfScopeSource:
    classification: Literal["out_of_scope"] = "out_of_scope"


ParseResult: TypeAlias = (
    ValidatedFriendshipEvent | InvalidFriendshipEvent | OutOfScopeSource
)


@dataclass(frozen=True, slots=True)
class LockedRecipientProjection:
    recipient_public_id: UUID
    registered_at: datetime
    friendship_state: StoredFriendshipState
    last_occurred_at_ms: int | None
    last_webhook_event_id: str | None

    def __post_init__(self) -> None:
        if self.friendship_state not in _STORED_FRIENDSHIP_STATES:
            raise ValueError("invalid stored friendship state")
        if (self.last_occurred_at_ms is None) != (
            self.last_webhook_event_id is None
        ):
            raise ValueError("friendship order fields must form a pair")
        if (
            self.last_occurred_at_ms is not None
            and (
                type(self.last_occurred_at_ms) is not int
                or self.last_occurred_at_ms < 0
            )
        ):
            raise ValueError("invalid friendship order timestamp")


@dataclass(frozen=True, slots=True)
class ProjectionTargetMissing:
    status: Literal["missing"] = "missing"


ProjectionTarget: TypeAlias = LockedRecipientProjection | ProjectionTargetMissing


@dataclass(frozen=True, slots=True)
class FriendshipAuditRecord:
    channel_public_id: UUID
    webhook_event_id: str
    event_type: FriendshipEventType
    occurred_at_ms: int
    outcome: ProjectionOutcome
    is_unblocked: bool | None

    def __post_init__(self) -> None:
        if self.event_type not in _EVENT_TYPES:
            raise ValueError("invalid audit event type")
        if type(self.occurred_at_ms) is not int or self.occurred_at_ms < 0:
            raise ValueError("invalid audit timestamp")
        if self.outcome not in _PROJECTION_OUTCOMES:
            raise ValueError("invalid audit outcome")
        if self.is_unblocked is not None and type(self.is_unblocked) is not bool:
            raise ValueError("invalid audit unblock flag")
        if self.event_type == "unfollow" and self.is_unblocked is not None:
            raise ValueError("unfollow audit cannot carry unblock flag")


@runtime_checkable
class FriendshipEventParser(Protocol):
    def parse(self, event: VerifiedWebhookEvent) -> ParseResult: ...


@runtime_checkable
class FriendshipSyncHandler(VerifiedEventHandler, Protocol):
    def handle(self, event: VerifiedWebhookEvent) -> HandlerOutcome: ...


@runtime_checkable
class AccountProjectionRepository(Protocol):
    def lock_target(
        self,
        *,
        channel_public_id: UUID,
        provider_id: str,
        subject: LineSubject,
    ) -> ProjectionTarget: ...

    def apply_locked(
        self,
        target: LockedRecipientProjection,
        *,
        friendship_state: FriendshipState,
        occurred_at_ms: int,
        webhook_event_id: str,
    ) -> None: ...


@runtime_checkable
class FriendshipAuditRepository(Protocol):
    def record(self, audit: FriendshipAuditRecord) -> None: ...
