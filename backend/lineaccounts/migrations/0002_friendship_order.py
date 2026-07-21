from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lineaccounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="deliveryrecipient",
            name="last_friendship_event_occurred_at_ms",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="deliveryrecipient",
            name="last_friendship_webhook_event_id",
            field=models.CharField(blank=True, max_length=26, null=True),
        ),
        migrations.AddConstraint(
            model_name="deliveryrecipient",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        last_friendship_event_occurred_at_ms__isnull=True,
                        last_friendship_webhook_event_id__isnull=True,
                    )
                    | models.Q(
                        last_friendship_event_occurred_at_ms__isnull=False,
                        last_friendship_webhook_event_id__isnull=False,
                    )
                ),
                name="lineacct_recip_friend_order_pair",
            ),
        ),
        migrations.AddConstraint(
            model_name="deliveryrecipient",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(last_friendship_event_occurred_at_ms__isnull=True)
                    | models.Q(last_friendship_event_occurred_at_ms__gte=0)
                ),
                name="lineacct_recip_friend_time_nonneg",
            ),
        ),
    ]
