import uuid

from django.core.exceptions import ValidationError
from django.db import models

from linechannels.models import LineChannel
from linechannels.validators import BoundaryValidationError, validate_provider_id


def validate_identity_provider_id(value: str) -> None:
    try:
        validate_provider_id(value)
    except BoundaryValidationError:
        raise ValidationError("invalid provider id") from None


class LineIdentity(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    provider_id = models.CharField(max_length=64, validators=[validate_identity_provider_id])
    subject = models.CharField(max_length=33)
    display_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("provider_id", "subject"),
                name="lineacct_identity_provider_subject_uniq",
            ),
        ]


class OwnerAccount(models.Model):
    class State(models.TextChoices):
        VACANT = "vacant", "Vacant"
        ACTIVE = "active", "Active"
        DEAUTHORIZATION_PENDING = (
            "deauthorization_pending",
            "Deauthorization pending",
        )
        LOCAL_DELETION_PENDING = "local_deletion_pending", "Local deletion pending"

    slot = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    state = models.CharField(max_length=32, choices=State, default=State.VACANT)
    identity = models.OneToOneField(
        LineIdentity,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="owner_account",
    )
    unlink_generation = models.UUIDField(null=True, blank=True, editable=False)
    line_deauthorized_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(slot=1),
                name="lineacct_owner_singleton_slot",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state="vacant",
                        identity__isnull=True,
                        unlink_generation__isnull=True,
                        line_deauthorized_at__isnull=True,
                    )
                    | models.Q(
                        state="active",
                        identity__isnull=False,
                        unlink_generation__isnull=True,
                        line_deauthorized_at__isnull=True,
                    )
                    | models.Q(
                        state="deauthorization_pending",
                        identity__isnull=False,
                        unlink_generation__isnull=False,
                        line_deauthorized_at__isnull=True,
                    )
                    | models.Q(
                        state="local_deletion_pending",
                        identity__isnull=False,
                        unlink_generation__isnull=False,
                        line_deauthorized_at__isnull=False,
                    )
                ),
                name="lineacct_owner_state_consistent",
            ),
        ]


class OwnerSession(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    owner = models.ForeignKey(
        OwnerAccount,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DeliveryRecipient(models.Model):
    class FriendshipState(models.TextChoices):
        FRIEND = "friend", "Friend"
        NOT_FRIEND = "not_friend", "Not friend"
        UNKNOWN = "unknown", "Unknown"

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    identity = models.ForeignKey(
        LineIdentity,
        on_delete=models.CASCADE,
        related_name="recipients",
    )
    line_channel = models.ForeignKey(
        LineChannel,
        on_delete=models.PROTECT,
        related_name="delivery_recipients",
    )
    enabled = models.BooleanField(default=True)
    friendship_state = models.CharField(
        max_length=16,
        choices=FriendshipState,
        default=FriendshipState.UNKNOWN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("identity", "line_channel"),
                name="lineacct_recipient_identity_channel_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("identity", "enabled"),
                name="lineacct_recip_ident_en_idx",
            ),
        ]
