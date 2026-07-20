from time import monotonic

from django.utils import timezone

from linechannels.container import build_webhook_credential_repository

from .audit import SafeWebhookAuditLogger
from .handlers import StaticHandlerRegistry
from .repositories import DjangoEventReceiptRepository
from .services import WebhookIngressService
from .verification import RawSignatureVerifier, WebhookPayloadValidator


def build_webhook_ingress_service() -> WebhookIngressService:
    return WebhookIngressService(
        credential_repository=build_webhook_credential_repository(),
        signature_verifier=RawSignatureVerifier(),
        payload_validator=WebhookPayloadValidator(),
        receipt_repository=DjangoEventReceiptRepository(),
        registry=StaticHandlerRegistry(),
        audit_logger=SafeWebhookAuditLogger(),
        monotonic_clock=monotonic,
        observed_at_clock=timezone.now,
    )
