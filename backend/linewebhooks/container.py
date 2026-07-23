from time import monotonic

from django.utils import timezone

from linechannels.container import build_webhook_credential_repository
from linefriendships.container import build_friendship_sync_handler

from .audit import SafeWebhookAuditLogger
from .handlers import StaticHandlerRegistry
from .repositories import DjangoEventReceiptRepository
from .services import WebhookIngressService
from .types import HandlerRegistration
from .verification import RawSignatureVerifier, WebhookPayloadValidator


_cached_service: WebhookIngressService | None = None


def build_webhook_ingress_service() -> WebhookIngressService:
    friendship_handler = build_friendship_sync_handler()
    return WebhookIngressService(
        credential_repository=build_webhook_credential_repository(),
        signature_verifier=RawSignatureVerifier(),
        payload_validator=WebhookPayloadValidator(),
        receipt_repository=DjangoEventReceiptRepository(),
        registry=StaticHandlerRegistry(
            (
                HandlerRegistration("follow", friendship_handler, "local"),
                HandlerRegistration("unfollow", friendship_handler, "local"),
            )
        ),
        audit_logger=SafeWebhookAuditLogger(),
        monotonic_clock=monotonic,
        observed_at_clock=timezone.now,
    )


def initialize_webhook_ingress_service() -> WebhookIngressService:
    global _cached_service
    if _cached_service is None:
        _cached_service = build_webhook_ingress_service()
    return _cached_service


def get_webhook_ingress_service() -> WebhookIngressService:
    if _cached_service is None:
        raise RuntimeError("webhook ingress service is not initialized")
    return _cached_service
