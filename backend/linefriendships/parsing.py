import re

from lineaccounts.types import LineSubject
from linewebhooks.types import FrozenJsonObject, VerifiedWebhookEvent

from .types import (
    InvalidFriendshipEvent,
    OutOfScopeSource,
    ParseResult,
    ValidatedFriendshipEvent,
)


_LINE_USER_ID = re.compile(r"U[0-9a-f]{32}\Z")


class DefaultFriendshipEventParser:
    def parse(self, event: VerifiedWebhookEvent) -> ParseResult:
        if event.event_type not in ("follow", "unfollow"):
            return InvalidFriendshipEvent()

        source = event.data.get("source")
        if not isinstance(source, FrozenJsonObject):
            return InvalidFriendshipEvent()

        source_type = source.get("type")
        if source_type in ("group", "room"):
            return OutOfScopeSource()
        if source_type != "user":
            return InvalidFriendshipEvent()

        user_id = source.get("userId")
        if not isinstance(user_id, str) or _LINE_USER_ID.fullmatch(user_id) is None:
            return InvalidFriendshipEvent()

        is_unblocked: bool | None = None
        if event.event_type == "follow" and "follow" in event.data:
            follow = event.data["follow"]
            if not isinstance(follow, FrozenJsonObject):
                return InvalidFriendshipEvent()
            if "isUnblocked" in follow:
                candidate = follow["isUnblocked"]
                if type(candidate) is not bool:
                    return InvalidFriendshipEvent()
                is_unblocked = candidate

        return ValidatedFriendshipEvent(
            channel_public_id=event.channel_public_id,
            webhook_event_id=event.webhook_event_id,
            event_type=event.event_type,
            occurred_at_ms=event.occurred_at_ms,
            subject=LineSubject(user_id),
            target_state=("friend" if event.event_type == "follow" else "not_friend"),
            is_unblocked=is_unblocked,
        )
