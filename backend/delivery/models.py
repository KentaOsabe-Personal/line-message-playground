from django.core.exceptions import ValidationError
from django.db import models


class DeliveryAttempt(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        UNKNOWN = "unknown", "Unknown"

    class TargetMode(models.TextChoices):
        FIXED_USER = "fixed_user", "Fixed user"

    class FailureType(models.TextChoices):
        CONFIGURATION = "configuration", "Configuration"
        INVALID_REQUEST = "invalid_request", "Invalid request"
        AUTHENTICATION = "authentication", "Authentication"
        PERMISSION = "permission", "Permission"
        CONFLICT = "conflict", "Conflict"
        RATE_LIMITED = "rate_limited", "Rate limited"
        SERVICE_UNAVAILABLE = "service_unavailable", "Service unavailable"
        TIMEOUT_UNKNOWN = "timeout_unknown", "Timeout unknown"
        PROCESSING_EXPIRED = "processing_expired", "Processing expired"
        UNEXPECTED = "unexpected", "Unexpected"

    operation_id = models.UUIDField(unique=True)
    subject = models.TextField()
    body = models.TextField()
    formatted_text = models.TextField()
    content_fingerprint = models.CharField(max_length=64, db_index=True)
    active_content_fingerprint = models.CharField(
        max_length=64,
        null=True,
        unique=True,
    )
    target_mode = models.CharField(
        max_length=32,
        choices=TargetMode,
        default=TargetMode.FIXED_USER,
    )
    status = models.CharField(
        max_length=16,
        choices=Status,
        default=Status.PROCESSING,
        db_index=True,
    )
    failure_type = models.CharField(
        max_length=32,
        choices=FailureType,
        null=True,
        blank=True,
    )
    line_request_id = models.CharField(max_length=255, null=True, blank=True)
    line_accepted_request_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    accepted_at = models.DateTimeField()
    processing_expires_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_mode="fixed_user"),
                name="delivery_target_mode_fixed_user",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="processing",
                        active_content_fingerprint__isnull=False,
                        failure_type__isnull=True,
                        sent_at__isnull=True,
                        failed_at__isnull=True,
                        completed_at__isnull=True,
                    )
                    | models.Q(
                        status="succeeded",
                        active_content_fingerprint__isnull=True,
                        failure_type__isnull=True,
                        sent_at__isnull=False,
                        failed_at__isnull=True,
                        completed_at__isnull=False,
                    )
                    | models.Q(
                        status__in=("failed", "unknown"),
                        active_content_fingerprint__isnull=True,
                        failure_type__isnull=False,
                        sent_at__isnull=True,
                        failed_at__isnull=False,
                        completed_at__isnull=False,
                    )
                ),
                name="delivery_attempt_valid_state",
            ),
        ]

    def __str__(self):
        return f"DeliveryAttempt(operation_id={self.operation_id}, status={self.status})"

    def _ensure_transition_allowed(self):
        if self.status != self.Status.PROCESSING:
            raise ValidationError("A terminal delivery attempt cannot be updated.")

    def mark_succeeded(
        self,
        *,
        completed_at,
        line_request_id=None,
        line_accepted_request_id=None,
    ):
        self._ensure_transition_allowed()
        self.status = self.Status.SUCCEEDED
        self.active_content_fingerprint = None
        self.sent_at = completed_at
        self.completed_at = completed_at
        self.line_request_id = line_request_id
        self.line_accepted_request_id = line_accepted_request_id
        self.save(
            update_fields=(
                "status",
                "active_content_fingerprint",
                "sent_at",
                "completed_at",
                "line_request_id",
                "line_accepted_request_id",
            )
        )

    def mark_unsuccessful(self, *, status, failure_type, completed_at):
        self._ensure_transition_allowed()
        if status not in (self.Status.FAILED, self.Status.UNKNOWN):
            raise ValidationError("An unsuccessful transition must be failed or unknown.")
        if failure_type not in self.FailureType.values:
            raise ValidationError("Unknown delivery failure type.")

        self.status = status
        self.active_content_fingerprint = None
        self.failure_type = failure_type
        self.failed_at = completed_at
        self.completed_at = completed_at
        self.save(
            update_fields=(
                "status",
                "active_content_fingerprint",
                "failure_type",
                "failed_at",
                "completed_at",
            )
        )
