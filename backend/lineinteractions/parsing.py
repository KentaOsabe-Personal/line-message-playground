import re

from lineaccounts.types import LineSubject
from linewebhooks.types import FrozenJsonObject, VerifiedWebhookEvent

from .types import (
    InvalidInteraction,
    OpaqueActionPayload,
    OutOfScopeInteraction,
    ParseResult,
    ParsedPostbackInteraction,
    ParsedTextInteraction,
    ReplyToken,
)


_LINE_USER_ID = re.compile(r"U[0-9a-f]{32}\Z")
_ACTION_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")


def _valid_utf16_length(value: object, minimum: int, maximum: int) -> bool:
    if not isinstance(value, str):
        return False
    try:
        code_units = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        return False
    return minimum <= code_units <= maximum


class DefaultInteractionParser:
    def parse(self, event: VerifiedWebhookEvent) -> ParseResult:
        if event.event_type not in ("message", "postback"):
            return InvalidInteraction()

        source = event.data.get("source")
        if not isinstance(source, FrozenJsonObject):
            return InvalidInteraction()
        source_type = source.get("type")
        if source_type in ("group", "room"):
            return OutOfScopeInteraction()
        if source_type != "user":
            return InvalidInteraction()
        subject = source.get("userId")
        if (
            not isinstance(subject, str)
            or _LINE_USER_ID.fullmatch(subject) is None
        ):
            return InvalidInteraction()

        reply_token = event.data.get("replyToken")
        if not _valid_utf16_length(reply_token, 1, 512):
            return InvalidInteraction()

        if event.event_type == "message":
            return self._parse_message(event, subject, reply_token)
        return self._parse_postback(event, subject, reply_token)

    @staticmethod
    def _parse_message(
        event: VerifiedWebhookEvent,
        subject: str,
        reply_token: str,
    ) -> ParseResult:
        message = event.data.get("message")
        if (
            not isinstance(message, FrozenJsonObject)
            or message.get("type") != "text"
        ):
            return InvalidInteraction()
        candidate = message.get("text")
        if not _valid_utf16_length(candidate, 1, 5000):
            return InvalidInteraction()
        return ParsedTextInteraction(
            subject=LineSubject(subject),
            reply_token=ReplyToken(reply_token),
            candidate=candidate,
        )

    @staticmethod
    def _parse_postback(
        event: VerifiedWebhookEvent,
        subject: str,
        reply_token: str,
    ) -> ParseResult:
        postback = event.data.get("postback")
        if not isinstance(postback, FrozenJsonObject):
            return InvalidInteraction()
        data = postback.get("data")
        if not _valid_utf16_length(data, 1, 300):
            return InvalidInteraction()
        parts = data.split(":", 2)
        if (
            len(parts) != 3
            or parts[0] != "v1"
            or _ACTION_NAME.fullmatch(parts[1]) is None
        ):
            return InvalidInteraction()
        return ParsedPostbackInteraction(
            subject=LineSubject(subject),
            reply_token=ReplyToken(reply_token),
            action_name=parts[1],
            payload=OpaqueActionPayload(parts[2]),
        )
