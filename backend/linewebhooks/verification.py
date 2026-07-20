import base64
import binascii
import hashlib
import hmac
import json
import re
from typing import Literal

from linechannels.types import ChannelSecret
from .types import (
    FrozenJsonObject,
    PayloadRejected,
    VerifiedEventData,
    VerifiedWebhookPayload,
)


_MAX_RAW_BODY_BYTES = 256 * 1024
_MAX_EVENTS = 10
_MAX_TIMESTAMP_MS = (2**63) - 1
_WEBHOOK_EVENT_ID_PATTERN = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}\Z")


class RawSignatureVerifier:
    def verify(
        self,
        raw_body: bytes,
        signature: str | None,
        channel_secret: ChannelSecret,
    ) -> Literal["verified", "rejected"]:
        if not isinstance(signature, str):
            return "rejected"

        try:
            supplied_text = signature.encode("ascii")
            supplied_digest = base64.b64decode(supplied_text, validate=True)
            if base64.b64encode(supplied_digest) != supplied_text:
                return "rejected"
            secret = channel_secret.reveal_for_use().encode("utf-8")
            expected_digest = hmac.new(secret, raw_body, hashlib.sha256).digest()
        except (UnicodeEncodeError, binascii.Error, TypeError, ValueError):
            return "rejected"

        if hmac.compare_digest(expected_digest, supplied_digest):
            return "verified"
        return "rejected"


class WebhookPayloadValidator:
    def validate(
        self,
        raw_body: bytes,
        expected_bot_user_id: str,
    ) -> VerifiedWebhookPayload | PayloadRejected:
        if len(raw_body) > _MAX_RAW_BODY_BYTES:
            return PayloadRejected()

        try:
            payload = json.loads(raw_body, parse_constant=self._reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return PayloadRejected()

        if not isinstance(payload, dict):
            return PayloadRejected()
        if payload.get("destination") != expected_bot_user_id:
            return PayloadRejected()

        events = payload.get("events")
        if not isinstance(events, list) or len(events) > _MAX_EVENTS:
            return PayloadRejected()

        verified_events: list[VerifiedEventData] = []
        for event in events:
            verified_event = self._validate_event(event)
            if verified_event is None:
                return PayloadRejected()
            verified_events.append(verified_event)
        return VerifiedWebhookPayload(events=tuple(verified_events))

    @staticmethod
    def _reject_json_constant(value: str) -> None:
        raise ValueError("invalid JSON constant")

    @staticmethod
    def _validate_event(event: object) -> VerifiedEventData | None:
        if not isinstance(event, dict):
            return None

        webhook_event_id = event.get("webhookEventId")
        event_type = event.get("type")
        occurred_at_ms = event.get("timestamp")
        delivery_context = event.get("deliveryContext")

        if not isinstance(webhook_event_id, str) or not _WEBHOOK_EVENT_ID_PATTERN.fullmatch(
            webhook_event_id
        ):
            return None
        if not isinstance(event_type, str) or not 1 <= len(event_type) <= 255:
            return None
        if (
            type(occurred_at_ms) is not int
            or occurred_at_ms < 0
            or occurred_at_ms > _MAX_TIMESTAMP_MS
        ):
            return None
        if not isinstance(delivery_context, dict):
            return None
        is_redelivery = delivery_context.get("isRedelivery")
        if not isinstance(is_redelivery, bool):
            return None

        return VerifiedEventData(
            webhook_event_id=webhook_event_id,
            event_type=event_type,
            occurred_at_ms=occurred_at_ms,
            is_redelivery=is_redelivery,
            event=FrozenJsonObject(event),
        )
