from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="WebhookEventReceipt",
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
                ("webhook_event_id", models.CharField(max_length=26, unique=True)),
                ("channel_public_id", models.UUIDField()),
                ("event_type", models.CharField(max_length=255)),
                ("occurred_at_ms", models.BigIntegerField()),
                ("is_redelivery", models.BooleanField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("processing", "Processing"),
                            ("processed", "Processed"),
                            ("failed", "Failed"),
                            ("unsupported", "Unsupported"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "failure_code",
                    models.CharField(
                        choices=[("handler_failed", "Handler failed")],
                        max_length=32,
                        null=True,
                    ),
                ),
                ("accepted_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "linewebhooks_event_receipt",
                "indexes": [
                    models.Index(
                        fields=["channel_public_id", "accepted_at"],
                        name="linewh_channel_accept_idx",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("occurred_at_ms__gte", 0)),
                        name="linewh_occurred_at_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                ("completed_at__isnull", True),
                                ("failure_code__isnull", True),
                                ("status", "processing"),
                            )
                            | models.Q(
                                ("completed_at__isnull", False),
                                ("failure_code__isnull", True),
                                ("status__in", ("processed", "unsupported")),
                            )
                            | models.Q(
                                ("completed_at__isnull", False),
                                ("failure_code", "handler_failed"),
                                ("failure_code__isnull", False),
                                ("status", "failed"),
                            )
                        ),
                        name="linewh_receipt_status_consistent",
                    ),
                ],
            },
        )
    ]
