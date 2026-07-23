from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="InteractionAudit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("channel_public_id", models.UUIDField()),
                (
                    "webhook_event_id",
                    models.CharField(max_length=26, unique=True),
                ),
                (
                    "event_type",
                    models.CharField(
                        choices=(
                            ("message", "Message"),
                            ("postback", "Postback"),
                        ),
                        max_length=16,
                    ),
                ),
                (
                    "operation_kind",
                    models.CharField(
                        choices=(
                            ("none", "None"),
                            ("command", "Command"),
                            ("action", "Action"),
                        ),
                        max_length=8,
                    ),
                ),
                (
                    "operation_identifier",
                    models.CharField(
                        max_length=64,
                        null=True,
                    ),
                ),
                (
                    "interaction_outcome",
                    models.CharField(
                        choices=(
                            ("command_processed", "Command processed"),
                            ("action_succeeded", "Action succeeded"),
                            ("action_no_change", "Action no change"),
                            ("action_rejected", "Action rejected"),
                            ("unknown", "Unknown"),
                            ("invalid", "Invalid"),
                            ("out_of_scope", "Out of scope"),
                            ("unlinked", "Unlinked"),
                            ("handler_failed", "Handler failed"),
                            ("processing_failed", "Processing failed"),
                            (
                                "credential_unavailable",
                                "Credential unavailable",
                            ),
                            ("deadline_exceeded", "Deadline exceeded"),
                        ),
                        max_length=32,
                    ),
                ),
                (
                    "reply_outcome",
                    models.CharField(
                        choices=(
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                            ("unknown", "Unknown"),
                            ("not_started", "Not started"),
                        ),
                        max_length=16,
                    ),
                ),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "lineinteractions_interaction_audit",
                "indexes": [
                    models.Index(
                        fields=("channel_public_id", "recorded_at"),
                        name="lineint_channel_recorded_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                event_type="message",
                                operation_kind="command",
                                operation_identifier=(
                                    "connectivity_ping_v1"
                                ),
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
                                operation_identifier=(
                                    "connectivity_ping_v1"
                                ),
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
                ],
            },
        ),
    ]
