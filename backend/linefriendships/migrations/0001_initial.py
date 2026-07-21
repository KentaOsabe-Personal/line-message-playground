from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="FriendshipSyncAudit",
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
                ("webhook_event_id", models.CharField(max_length=26)),
                (
                    "event_type",
                    models.CharField(
                        choices=[("follow", "Follow"), ("unfollow", "Unfollow")],
                        max_length=16,
                    ),
                ),
                ("occurred_at_ms", models.BigIntegerField()),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("applied", "Applied"),
                            ("state_maintained", "State maintained"),
                            ("stale", "Stale"),
                            ("duplicate", "Duplicate"),
                            ("unlinked", "Unlinked"),
                            ("unresolvable", "Unresolvable"),
                            ("out_of_scope", "Out of scope"),
                            ("invalid", "Invalid"),
                        ],
                        max_length=24,
                    ),
                ),
                ("is_unblocked", models.BooleanField(blank=True, null=True)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["webhook_event_id", "recorded_at"],
                        name="linefriend_event_recorded_idx",
                    ),
                    models.Index(
                        fields=["channel_public_id", "recorded_at"],
                        name="linefriend_chan_recorded_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(event_type__in=("follow", "unfollow")),
                        name="linefriend_audit_event_type_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            outcome__in=(
                                "applied",
                                "state_maintained",
                                "stale",
                                "duplicate",
                                "unlinked",
                                "unresolvable",
                                "out_of_scope",
                                "invalid",
                            )
                        ),
                        name="linefriend_audit_outcome_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(occurred_at_ms__gte=0),
                        name="linefriend_audit_time_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(event_type="follow")
                            | models.Q(is_unblocked__isnull=True)
                        ),
                        name="linefriend_audit_unfollow_no_unblock",
                    ),
                ],
            },
        ),
    ]
