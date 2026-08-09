import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Length
from django.db.models.lookups import GreaterThan


class LineChannel(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    messaging_api_channel_id = models.CharField(max_length=64, unique=True)
    bot_user_id = models.CharField(max_length=33, unique=True)
    label = models.CharField(max_length=255)
    provider_id = models.CharField(max_length=64, null=True)
    is_active = models.BooleanField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=("provider_id", "is_active"), name="linech_provider_active_idx")]

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._original_public_id = instance.public_id
        return instance

    @property
    def credentials_configured(self) -> bool:
        try:
            self.credential
        except LineChannelCredential.DoesNotExist:
            return False
        return True

    def save(self, *args, **kwargs):
        original_public_id = getattr(self, "_original_public_id", self.public_id)
        if self.pk is not None and self.public_id != original_public_id:
            raise ValidationError("public_id is immutable")
        super().save(*args, **kwargs)
        self._original_public_id = self.public_id

    def __str__(self) -> str:
        return (
            f"LineChannel(public_id={self.public_id}, active={self.is_active}, "
            f"credentials_configured={self.credentials_configured})"
        )

    __repr__ = __str__


class LineChannelCredential(models.Model):
    line_channel = models.OneToOneField(
        LineChannel,
        on_delete=models.PROTECT,
        primary_key=True,
        related_name="credential",
    )
    access_token_ciphertext = models.BinaryField(editable=False)
    channel_secret_ciphertext = models.BinaryField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=GreaterThan(Length("access_token_ciphertext"), 0),
                name="linechannels_access_ciphertext_nonempty",
            ),
            models.CheckConstraint(
                condition=GreaterThan(Length("channel_secret_ciphertext"), 0),
                name="linechannels_secret_ciphertext_nonempty",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"LineChannelCredential(public_id={self.line_channel.public_id}, "
            "credentials_configured=True)"
        )

    __repr__ = __str__


class ChannelDeactivationState(models.Model):
    class Status(models.TextChoices):
        CHECKING = "checking"
        UNLINKING = "unlinking"
        CONFIRMATION_REQUIRED = "confirmation_required"
        COMPLETED = "completed"

    line_channel = models.OneToOneField(
        LineChannel,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="deactivation_state",
    )
    operation_id = models.UUIDField(unique=True, editable=False)
    owner_identity_public_id = models.UUIDField(editable=False)
    provider_id = models.CharField(max_length=64, editable=False)
    expected_channel_revision = models.DateTimeField(editable=False)
    status = models.CharField(max_length=32, choices=Status.choices, editable=False)
    safe_reason = models.CharField(max_length=64, null=True, editable=False)
    subject_rich_operation_id = models.UUIDField(null=True, editable=False)
    latest_recovery_operation_id = models.UUIDField(null=True, editable=False)
    recovery_result_ready = models.BooleanField(default=False, editable=False)
    accepted_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)
    completed_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
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
            models.CheckConstraint(
                condition=GreaterThan(Length("provider_id"), 0),
                name="linech_deactivation_provider_nonempty",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(recovery_result_ready=False)
                    | models.Q(latest_recovery_operation_id__isnull=False)
                ),
                name="linech_deactivation_recovery_ready_valid",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"ChannelDeactivationState(operation_id={self.operation_id}, "
            f"status={self.status})"
        )

    __repr__ = __str__
