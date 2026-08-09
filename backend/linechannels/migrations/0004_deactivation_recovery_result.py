from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("linechannels", "0003_channel_deactivation_state")]

    operations = [
        migrations.AddField(
            model_name="channeldeactivationstate",
            name="recovery_result_ready",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddConstraint(
            model_name="channeldeactivationstate",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(recovery_result_ready=False)
                    | models.Q(latest_recovery_operation_id__isnull=False)
                ),
                name="linech_deactivation_recovery_ready_valid",
            ),
        ),
    ]
