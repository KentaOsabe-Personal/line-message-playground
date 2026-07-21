from time import monotonic

from django.utils import timezone

from linechannels.container import build_webhook_credential_repository
from linefriendships.container import build_friendship_sync_handler

from .audit import SafeWebhookAuditLogger
from .handlers import StaticHandlerRegistry
from .repositories import DjangoEventReceiptRepository
from .services import WebhookIngressService
from .verification import RawSignatureVerifier, WebhookPayloadValidator


def build_webhook_ingress_service() -> WebhookIngressService:
    friendship_handler = build_friendship_sync_handler()
    return WebhookIngressService(
        credential_repository=build_webhook_credential_repository(),
        signature_verifier=RawSignatureVerifier(),
        payload_validator=WebhookPayloadValidator(),
        receipt_repository=DjangoEventReceiptRepository(),
        registry=StaticHandlerRegistry(
            (
                ("follow", friendship_handler),
                ("unfollow", friendship_handler),
            )
        ),
        audit_logger=SafeWebhookAuditLogger(),
        monotonic_clock=monotonic,
        observed_at_clock=timezone.now,
    )
