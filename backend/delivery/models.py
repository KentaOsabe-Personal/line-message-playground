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
        LINKED_RECIPIENT = "linked_recipient", "Linked recipient"

    class FriendshipState(models.TextChoices):
        FRIEND = "friend", "Friend"
        NOT_FRIEND = "not_friend", "Not friend"
        UNKNOWN = "unknown", "Unknown"

    class FailureType(models.TextChoices):
        CONFIGURATION = "configuration", "Configuration"
        INVALID_REQUEST = "invalid_request", "Invalid request"
        AUTHENTICATION = "authentication", "Authentication"
        PERMISSION = "permission", "Permission"
        CONFLICT = "conflict", "Conflict"
        RATE_LIMITED = "rate_limited", "Rate limited"
        SERVICE_UNKNOWN = "service_unknown", "Service unknown"
        SERVICE_UNAVAILABLE = "service_unavailable", "Service unavailable"
        TIMEOUT_UNKNOWN = "timeout_unknown", "Timeout unknown"
        RESPONSE_UNKNOWN = "response_unknown", "Response unknown"
        PROCESSING_EXPIRED = "processing_expired", "Processing expired"
        TARGET_CHANGED = "target_changed", "Target changed"
        STORAGE_UNAVAILABLE = "storage_unavailable", "Storage unavailable"
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
    request_fingerprint = models.CharField(max_length=64)
    active_request_fingerprint = models.CharField(
        max_length=64,
        null=True,
        unique=True,
    )
    target_mode = models.CharField(
        max_length=32,
        choices=TargetMode,
        default=TargetMode.FIXED_USER,
    )
    owner_principal_slot = models.PositiveSmallIntegerField()
    owner_identity_public_id = models.UUIDField(null=True)
    channel_public_id = models.UUIDField(null=True)
    channel_label_snapshot = models.CharField(max_length=255, null=True)
    recipient_public_id = models.UUIDField(null=True)
    channel_active_snapshot = models.BooleanField(null=True)
    recipient_enabled_snapshot = models.BooleanField(null=True)
    friendship_state_snapshot = models.CharField(
        max_length=16,
        choices=FriendshipState,
        null=True,
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
    receipt_requested = models.BooleanField(default=False)
    receipt_expires_at = models.DateTimeField(null=True, blank=True)
    receipt_token_digest = models.CharField(
        max_length=64,
        null=True,
        unique=True,
    )
    receipt_confirmed_at = models.DateTimeField(null=True, blank=True)
    receipt_webhook_event_id = models.CharField(
        max_length=26,
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=("owner_principal_slot", "operation_id"),
                name="delivery_owner_operation_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(target_mode="fixed_user")
                    | models.Q(
                        target_mode="linked_recipient",
                        owner_principal_slot__isnull=False,
                        request_fingerprint__isnull=False,
                        owner_identity_public_id__isnull=False,
                        channel_public_id__isnull=False,
                        channel_label_snapshot__isnull=False,
                        recipient_public_id__isnull=False,
                        channel_active_snapshot__isnull=False,
                        recipient_enabled_snapshot__isnull=False,
                        friendship_state_snapshot__isnull=False,
                        friendship_state_snapshot__in=(
                            "friend",
                            "not_friend",
                            "unknown",
                        ),
                    )
                ),
                name="delivery_attempt_valid_target",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="processing",
                        failure_type__isnull=True,
                        sent_at__isnull=True,
                        failed_at__isnull=True,
                        completed_at__isnull=True,
                    )
                    & (
                        models.Q(
                            target_mode="fixed_user",
                            active_content_fingerprint__isnull=False,
                            active_request_fingerprint__isnull=True,
                        )
                        | models.Q(
                            target_mode="linked_recipient",
                            active_content_fingerprint__isnull=True,
                            active_request_fingerprint__isnull=False,
                            active_request_fingerprint=models.F(
                                "request_fingerprint"
                            ),
                        )
                    )
                    | models.Q(
                        status="succeeded",
                        active_content_fingerprint__isnull=True,
                        active_request_fingerprint__isnull=True,
                        failure_type__isnull=True,
                        sent_at__isnull=False,
                        failed_at__isnull=True,
                        completed_at__isnull=False,
                    )
                    | models.Q(
                        status__in=("failed", "unknown"),
                        active_content_fingerprint__isnull=True,
                        active_request_fingerprint__isnull=True,
                        failure_type__isnull=False,
                        sent_at__isnull=True,
                        failed_at__isnull=False,
                        completed_at__isnull=False,
                    )
                ),
                name="delivery_attempt_valid_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        receipt_requested=False,
                        receipt_expires_at__isnull=True,
                        receipt_token_digest__isnull=True,
                        receipt_confirmed_at__isnull=True,
                        receipt_webhook_event_id__isnull=True,
                    )
                    | (
                        models.Q(
                            receipt_requested=True,
                            receipt_expires_at__isnull=False,
                            receipt_token_digest__isnull=False,
                        )
                        & (
                            models.Q(
                                receipt_confirmed_at__isnull=True,
                                receipt_webhook_event_id__isnull=True,
                            )
                            | models.Q(
                                receipt_confirmed_at__isnull=False,
                                receipt_webhook_event_id__isnull=False,
                            )
                        )
                    )
                ),
                name="delivery_attempt_valid_receipt",
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
        self.active_request_fingerprint = None
        self.sent_at = completed_at
        self.completed_at = completed_at
        self.line_request_id = line_request_id
        self.line_accepted_request_id = line_accepted_request_id
        self.save(
            update_fields=(
                "status",
                "active_content_fingerprint",
                "active_request_fingerprint",
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
        self.active_request_fingerprint = None
        self.failure_type = failure_type
        self.failed_at = completed_at
        self.completed_at = completed_at
        self.save(
            update_fields=(
                "status",
                "active_content_fingerprint",
                "active_request_fingerprint",
                "failure_type",
                "failed_at",
                "completed_at",
            )
        )
