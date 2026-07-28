"""Delivery aggregateのproduction dependencyを構築するcomposition root。"""

from collections.abc import Callable
from datetime import datetime

from django.utils import timezone

from lineaccounts.delivery_repositories import DeliveryTargetDirectory
from linechannels.container import build_credential_repository

from .confirmation import ConfirmationService
from .gateway import LINEChannelPushGateway
from .receipt import ReceiptCapabilityFactory, ReceiptHandler
from .repositories import DjangoAttemptRepository
from .services import DeliveryService


def build_target_directory() -> DeliveryTargetDirectory:
    return DeliveryTargetDirectory()


def build_confirmation_service(
    *,
    clock: Callable[[], datetime] = timezone.now,
) -> ConfirmationService:
    return ConfirmationService(clock=clock)


def build_delivery_service(
    *,
    clock: Callable[[], datetime] = timezone.now,
) -> DeliveryService:
    return DeliveryService(
        clock=clock,
        target_directory=build_target_directory(),
        attempt_repository=DjangoAttemptRepository(clock=clock),
        receipt_capability_factory=ReceiptCapabilityFactory(),
        credential_repository=build_credential_repository(),
        channel_push_gateway=LINEChannelPushGateway(),
    )


def build_status_service(
    *,
    clock: Callable[[], datetime] = timezone.now,
) -> DeliveryService:
    return DeliveryService(
        clock=clock,
        attempt_repository=DjangoAttemptRepository(clock=clock),
    )


def build_receipt_handler(
    *,
    clock: Callable[[], datetime] = timezone.now,
) -> ReceiptHandler:
    return ReceiptHandler(
        attempt_repository=DjangoAttemptRepository(clock=clock),
        clock=clock,
    )
