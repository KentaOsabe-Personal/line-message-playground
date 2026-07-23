from django.db import models


class InteractionAudit(models.Model):
    class EventType(models.TextChoices):
        MESSAGE = "message", "Message"
        POSTBACK = "postback", "Postback"

    class OperationKind(models.TextChoices):
        NONE = "none", "None"
        COMMAND = "command", "Command"
        ACTION = "action", "Action"

    class InteractionOutcome(models.TextChoices):
        COMMAND_PROCESSED = "command_processed", "Command processed"
        ACTION_SUCCEEDED = "action_succeeded", "Action succeeded"
        ACTION_NO_CHANGE = "action_no_change", "Action no change"
        ACTION_REJECTED = "action_rejected", "Action rejected"
        UNKNOWN = "unknown", "Unknown"
        INVALID = "invalid", "Invalid"
        OUT_OF_SCOPE = "out_of_scope", "Out of scope"
        UNLINKED = "unlinked", "Unlinked"
        HANDLER_FAILED = "handler_failed", "Handler failed"
        PROCESSING_FAILED = "processing_failed", "Processing failed"
        CREDENTIAL_UNAVAILABLE = (
            "credential_unavailable",
            "Credential unavailable",
        )
        DEADLINE_EXCEEDED = "deadline_exceeded", "Deadline exceeded"

    class ReplyOutcome(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        UNKNOWN = "unknown", "Unknown"
        NOT_STARTED = "not_started", "Not started"

    channel_public_id = models.UUIDField()
    webhook_event_id = models.CharField(max_length=26, unique=True)
    event_type = models.CharField(max_length=16, choices=EventType)
    operation_kind = models.CharField(max_length=8, choices=OperationKind)
    operation_identifier = models.CharField(
        max_length=64,
        null=True,
    )
    interaction_outcome = models.CharField(
        max_length=32,
        choices=InteractionOutcome,
    )
    reply_outcome = models.CharField(max_length=16, choices=ReplyOutcome)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "lineinteractions_interaction_audit"
        indexes = [
            models.Index(
                fields=("channel_public_id", "recorded_at"),
                name="lineint_channel_recorded_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        event_type="message",
                        operation_kind="command",
                        operation_identifier="connectivity_ping_v1",
                        operation_identifier__isnull=False,
                        interaction_outcome="command_processed",
                        reply_outcome__in=(
                            "accepted",
                            "rejected",
                            "unknown",
                        ),
                    )
                    | models.Q(
                        event_type="message",
                        operation_kind="command",
                        operation_identifier="connectivity_ping_v1",
                        operation_identifier__isnull=False,
                        interaction_outcome__in=(
                            "processing_failed",
                            "credential_unavailable",
                            "deadline_exceeded",
                        ),
                        reply_outcome="not_started",
                    )
                    | models.Q(
                        event_type="postback",
                        operation_kind="action",
                        operation_identifier__isnull=False,
                        operation_identifier__gt="",
                        interaction_outcome__in=(
                            "action_succeeded",
                            "action_no_change",
                            "action_rejected",
                            "handler_failed",
                        ),
                        reply_outcome="not_started",
                    )
                    | models.Q(
                        event_type__in=("message", "postback"),
                        operation_kind="none",
                        operation_identifier__isnull=True,
                        interaction_outcome__in=(
                            "unknown",
                            "invalid",
                            "out_of_scope",
                            "unlinked",
                            "processing_failed",
                        ),
                        reply_outcome="not_started",
                    )
                ),
                name="lineint_audit_result_consistent",
            ),
        ]
