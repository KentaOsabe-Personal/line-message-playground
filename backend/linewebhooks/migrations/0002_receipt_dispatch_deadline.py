from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("linewebhooks", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="webhookeventreceipt",
            name="failure_code",
            field=models.CharField(
                choices=[
                    ("handler_failed", "Handler failed"),
                    (
                        "dispatch_deadline_exceeded",
                        "Dispatch deadline exceeded",
                    ),
                ],
                max_length=32,
                null=True,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="webhookeventreceipt",
            name="linewh_receipt_status_consistent",
        ),
        migrations.AddConstraint(
            model_name="webhookeventreceipt",
            constraint=models.CheckConstraint(
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
                        (
                            "failure_code__in",
                            (
                                "handler_failed",
                                "dispatch_deadline_exceeded",
                            ),
                        ),
                        ("failure_code__isnull", False),
                        ("status", "failed"),
                    )
                ),
                name="linewh_receipt_status_consistent",
            ),
        ),
    ]
