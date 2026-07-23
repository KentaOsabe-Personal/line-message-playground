import base64
import hashlib
import hmac
import json
import threading
from collections.abc import Callable
from datetime import datetime
from time import monotonic
from uuid import UUID

from django.utils import timezone

from linechannels.types import ChannelSecret, WebhookChannelAvailable
from linewebhooks.handlers import StaticHandlerRegistry
from linewebhooks.repositories import DjangoEventReceiptRepository
from linewebhooks.services import WebhookIngressService
from linewebhooks.types import (
    HandlerExecutionContext,
    HandlerRegistration,
    HandlerSucceeded,
    WebhookAuditEntry,
)
from linewebhooks.verification import RawSignatureVerifier, WebhookPayloadValidator


CHANNEL_ID = UUID("12345678-1234-4234-9234-123456789abc")
BOT_USER_ID = "U" + "1" * 32
CHANNEL_SECRET = "integration-channel-secret"
EVENT_IDS = tuple(f"01ARZ3NDEKTSV4RRFFQ69G5FA{suffix}" for suffix in "VWXYZ0")


class FixedCredentialRepository:
    def get(self, channel_public_id: UUID) -> WebhookChannelAvailable:
        return WebhookChannelAvailable(
            channel_public_id=channel_public_id,
            bot_user_id=BOT_USER_ID,
            channel_secret=ChannelSecret(CHANNEL_SECRET),
        )


class CapturingAuditLogger:
    def __init__(self) -> None:
        self.entries: list[WebhookAuditEntry] = []
        self._lock = threading.Lock()

    def record(self, entry: WebhookAuditEntry) -> None:
        with self._lock:
            self.entries.append(entry)


class RecordingHandler:
    def __init__(
        self,
        result: object | None = None,
        *,
        callback: Callable[[object], None] | None = None,
    ) -> None:
        self.result = result if result is not None else HandlerSucceeded()
        self.callback = callback
        self.events: list[object] = []
        self.contexts: list[HandlerExecutionContext] = []
        self._lock = threading.Lock()

    def handle(
        self,
        event: object,
        context: HandlerExecutionContext,
    ) -> object:
        if self.callback is not None:
            self.callback(event)
        with self._lock:
            self.events.append(event)
            self.contexts.append(context)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def event(
    webhook_event_id: str,
    *,
    event_type: str = "message",
    occurred_at_ms: int = 100,
    is_redelivery: bool = False,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "webhookEventId": webhook_event_id,
        "type": event_type,
        "timestamp": occurred_at_ms,
        "deliveryContext": {"isRedelivery": is_redelivery},
    }
    if extra:
        value.update(extra)
    return value


def signed_payload(
    events: list[dict[str, object]],
    *,
    destination: str = BOT_USER_ID,
) -> tuple[bytes, str]:
    raw_body = json.dumps(
        {"destination": destination, "events": events},
        separators=(",", ":"),
    ).encode("utf-8")
    return raw_body, sign_raw_body(raw_body)


def sign_raw_body(raw_body: bytes) -> str:
    return base64.b64encode(
        hmac.new(CHANNEL_SECRET.encode(), raw_body, hashlib.sha256).digest()
    ).decode("ascii")


def build_service(
    *,
    handler: RecordingHandler | None = None,
    credential_repository: object | None = None,
    receipt_repository: object | None = None,
    audit_logger: CapturingAuditLogger | None = None,
    monotonic_clock: Callable[[], float] = monotonic,
    observed_at_clock: Callable[[], datetime] = timezone.now,
) -> tuple[WebhookIngressService, CapturingAuditLogger]:
    audit = audit_logger or CapturingAuditLogger()
    registrations = (
        (HandlerRegistration("message", handler, "local"),)
        if handler is not None
        else ()
    )
    return (
        WebhookIngressService(
            credential_repository=(
                credential_repository or FixedCredentialRepository()
            ),
            signature_verifier=RawSignatureVerifier(),
            payload_validator=WebhookPayloadValidator(),
            receipt_repository=(
                receipt_repository or DjangoEventReceiptRepository()
            ),
            registry=StaticHandlerRegistry(registrations),
            audit_logger=audit,
            monotonic_clock=monotonic_clock,
            observed_at_clock=observed_at_clock,
        ),
        audit,
    )
