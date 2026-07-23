from collections.abc import Callable, Iterable
from time import monotonic

from django.utils import timezone

from linechannels.container import build_webhook_credential_repository
from linefriendships.container import build_friendship_sync_handler
from lineinteractions.container import build_interaction_handler
from lineinteractions.types import PostbackActionHandler

from .audit import SafeWebhookAuditLogger
from .handlers import StaticHandlerRegistry
from .repositories import DjangoEventReceiptRepository
from .services import WebhookIngressService
from .types import HandlerRegistration
from .verification import RawSignatureVerifier, WebhookPayloadValidator


_cached_service: WebhookIngressService | None = None


def build_webhook_ingress_service(
    *,
    action_registrations: Iterable[
        tuple[str, PostbackActionHandler]
    ] = (),
    monotonic_clock: Callable[[], float] = monotonic,
) -> WebhookIngressService:
    friendship_handler = build_friendship_sync_handler()
    interaction_handler = build_interaction_handler(
        action_registrations=action_registrations,
        monotonic_clock=monotonic_clock,
    )
    return WebhookIngressService(
        credential_repository=build_webhook_credential_repository(),
        signature_verifier=RawSignatureVerifier(),
        payload_validator=WebhookPayloadValidator(),
        receipt_repository=DjangoEventReceiptRepository(),
        registry=StaticHandlerRegistry(
            (
                HandlerRegistration("follow", friendship_handler, "local"),
                HandlerRegistration("unfollow", friendship_handler, "local"),
                HandlerRegistration(
                    "message",
                    interaction_handler,
                    "deadline_managed_external",
                ),
                HandlerRegistration(
                    "postback",
                    interaction_handler,
                    "deadline_managed_external",
                ),
            )
        ),
        audit_logger=SafeWebhookAuditLogger(),
        monotonic_clock=monotonic_clock,
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
