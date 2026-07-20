from datetime import datetime
from typing import Literal, TypeAlias

from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from .models import WebhookEventReceipt
from .types import (
    ReceiptCandidate,
    ReceiptDecision,
    ReceiptStorageFailed,
)


FinalizationResult: TypeAlias = Literal["updated", "unchanged", "failed"]


class DjangoEventReceiptRepository:
    def __init__(self, using: str = "default") -> None:
        self.using = using

    def accept_batch(
        self,
        candidates: tuple[ReceiptCandidate, ...],
    ) -> tuple[ReceiptDecision, ...] | ReceiptStorageFailed:
        decisions_by_event_id: dict[str, ReceiptDecision] = {}
        decisions: list[ReceiptDecision] = []
        try:
            with transaction.atomic(using=self.using):
                for candidate in candidates:
                    prior = decisions_by_event_id.get(candidate.webhook_event_id)
                    if prior is not None:
                        decisions.append(
                            ReceiptDecision(
                                receipt_id=prior.receipt_id,
                                webhook_event_id=prior.webhook_event_id,
                                status=prior.status,
                                created=False,
                            )
                        )
                        continue

                    try:
                        with transaction.atomic(using=self.using):
                            receipt = self._create_receipt(candidate)
                        created = True
                    except IntegrityError:
                        receipt = WebhookEventReceipt.objects.using(self.using).get(
                            webhook_event_id=candidate.webhook_event_id
                        )
                        created = False

                    decision = self._decision(receipt, created=created)
                    decisions_by_event_id[candidate.webhook_event_id] = decision
                    decisions.append(decision)
        except DatabaseError:
            return ReceiptStorageFailed()
        return tuple(decisions)

    def _create_receipt(self, candidate: ReceiptCandidate) -> WebhookEventReceipt:
        completed_at: datetime | None = None
        if candidate.initial_status == WebhookEventReceipt.Status.UNSUPPORTED:
            completed_at = timezone.now()
        return WebhookEventReceipt.objects.using(self.using).create(
            channel_public_id=candidate.channel_public_id,
            webhook_event_id=candidate.webhook_event_id,
            event_type=candidate.event_type,
            occurred_at_ms=candidate.occurred_at_ms,
            is_redelivery=candidate.is_redelivery,
            status=candidate.initial_status,
            failure_code=None,
            completed_at=completed_at,
        )

    def mark_processed(self, receipt_id: int) -> FinalizationResult:
        return self._finalize(
            receipt_id,
            status=WebhookEventReceipt.Status.PROCESSED,
            failure_code=None,
        )

    def mark_failed(
        self,
        receipt_id: int,
        code: Literal["handler_failed"],
    ) -> FinalizationResult:
        if code != WebhookEventReceipt.FailureCode.HANDLER_FAILED:
            raise ValueError("unsupported receipt failure code")
        return self._finalize(
            receipt_id,
            status=WebhookEventReceipt.Status.FAILED,
            failure_code=code,
        )

    def _finalize(
        self,
        receipt_id: int,
        *,
        status: str,
        failure_code: str | None,
    ) -> FinalizationResult:
        try:
            updated = self._conditional_update(
                receipt_id,
                status=status,
                failure_code=failure_code,
            )
        except DatabaseError:
            return "failed"
        return "updated" if updated == 1 else "unchanged"

    def _conditional_update(
        self,
        receipt_id: int,
        *,
        status: str,
        failure_code: str | None,
    ) -> int:
        completed_at = timezone.now()
        return (
            WebhookEventReceipt.objects.using(self.using)
            .filter(
                pk=receipt_id,
                status=WebhookEventReceipt.Status.PROCESSING,
            )
            .update(
                status=status,
                failure_code=failure_code,
                completed_at=completed_at,
                updated_at=completed_at,
            )
        )

    @staticmethod
    def _decision(
        receipt: WebhookEventReceipt,
        *,
        created: bool,
    ) -> ReceiptDecision:
        return ReceiptDecision(
            receipt_id=receipt.pk,
            webhook_event_id=receipt.webhook_event_id,
            status=receipt.status,
            created=created,
        )
