from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("linechannels", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="linechannel",
            name="provider_id",
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.AddIndex(
            model_name="linechannel",
            index=models.Index(
                fields=["provider_id", "is_active"],
                name="linech_provider_active_idx",
            ),
        ),
    ]
