from django.db import models


class FriendshipSyncAudit(models.Model):
    class EventType(models.TextChoices):
        FOLLOW = "follow", "Follow"
        UNFOLLOW = "unfollow", "Unfollow"

    class Outcome(models.TextChoices):
        APPLIED = "applied", "Applied"
        STATE_MAINTAINED = "state_maintained", "State maintained"
        STALE = "stale", "Stale"
        DUPLICATE = "duplicate", "Duplicate"
        UNLINKED = "unlinked", "Unlinked"
        UNRESOLVABLE = "unresolvable", "Unresolvable"
        OUT_OF_SCOPE = "out_of_scope", "Out of scope"
        INVALID = "invalid", "Invalid"

    channel_public_id = models.UUIDField()
    webhook_event_id = models.CharField(max_length=26)
    event_type = models.CharField(max_length=16, choices=EventType)
    occurred_at_ms = models.BigIntegerField()
    outcome = models.CharField(max_length=24, choices=Outcome)
    is_unblocked = models.BooleanField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("webhook_event_id", "recorded_at"),
                name="linefriend_event_recorded_idx",
            ),
            models.Index(
                fields=("channel_public_id", "recorded_at"),
                name="linefriend_chan_recorded_idx",
            ),
        ]
        constraints = [
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
        ]
