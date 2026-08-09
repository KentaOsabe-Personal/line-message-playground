import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Length
from django.db.models.lookups import GreaterThan


class Migration(migrations.Migration):
    dependencies = [("linechannels", "0002_linechannel_provider_id")]

    operations = [
        migrations.CreateModel(
            name="ChannelDeactivationState",
            fields=[
                (
                    "line_channel",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="deactivation_state",
                        serialize=False,
                        to="linechannels.linechannel",
                    ),
                ),
                ("operation_id", models.UUIDField(editable=False, unique=True)),
                ("owner_identity_public_id", models.UUIDField(editable=False)),
                ("provider_id", models.CharField(editable=False, max_length=64)),
                ("expected_channel_revision", models.DateTimeField(editable=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("checking", "Checking"),
                            ("unlinking", "Unlinking"),
                            ("confirmation_required", "Confirmation Required"),
                            ("completed", "Completed"),
                        ],
                        editable=False,
                        max_length=32,
                    ),
                ),
                ("safe_reason", models.CharField(editable=False, max_length=64, null=True)),
                ("subject_rich_operation_id", models.UUIDField(editable=False, null=True)),
                ("latest_recovery_operation_id", models.UUIDField(editable=False, null=True)),
                ("accepted_at", models.DateTimeField(auto_now_add=True, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True, editable=False)),
                ("completed_at", models.DateTimeField(editable=False, null=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name="channeldeactivationstate",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="checking",
                        safe_reason__isnull=True,
                        subject_rich_operation_id__isnull=True,
                        latest_recovery_operation_id__isnull=True,
                        completed_at__isnull=True,
                    )
                    | models.Q(
                        status="unlinking",
                        safe_reason__isnull=True,
                        subject_rich_operation_id__isnull=False,
                        latest_recovery_operation_id__isnull=True,
                        completed_at__isnull=True,
                    )
                    | models.Q(
                        status="confirmation_required",
                        safe_reason__isnull=False,
                        completed_at__isnull=True,
                    )
                    | models.Q(
                        status="completed",
                        safe_reason__isnull=True,
                        subject_rich_operation_id__isnull=True,
                        latest_recovery_operation_id__isnull=True,
                        completed_at__isnull=False,
                    )
                ),
                name="linech_deactivation_variant_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="channeldeactivationstate",
            constraint=models.CheckConstraint(
                condition=GreaterThan(Length("provider_id"), 0),
                name="linech_deactivation_provider_nonempty",
            ),
        ),
    ]
