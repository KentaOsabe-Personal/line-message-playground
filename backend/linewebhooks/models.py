from django.db import models
from django.db.models import Q


class WebhookEventReceipt(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"
        UNSUPPORTED = "unsupported", "Unsupported"

    class FailureCode(models.TextChoices):
        HANDLER_FAILED = "handler_failed", "Handler failed"

    webhook_event_id = models.CharField(max_length=26, unique=True)
    channel_public_id = models.UUIDField()
    event_type = models.CharField(max_length=255)
    occurred_at_ms = models.BigIntegerField()
    is_redelivery = models.BooleanField()
    status = models.CharField(max_length=16, choices=Status.choices)
    failure_code = models.CharField(
        max_length=32,
        choices=FailureCode.choices,
        null=True,
    )
    accepted_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "linewebhooks_event_receipt"
        indexes = [
            models.Index(
                fields=("channel_public_id", "accepted_at"),
                name="linewh_channel_accept_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(occurred_at_ms__gte=0),
                name="linewh_occurred_at_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="processing",
                        completed_at__isnull=True,
                        failure_code__isnull=True,
                    )
                    | Q(
                        status__in=("processed", "unsupported"),
                        completed_at__isnull=False,
                        failure_code__isnull=True,
                    )
                    | Q(
                        status="failed",
                        completed_at__isnull=False,
                        failure_code__isnull=False,
                        failure_code="handler_failed",
                    )
                ),
                name="linewh_receipt_status_consistent",
            ),
        ]
