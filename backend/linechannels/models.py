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
    is_active = models.BooleanField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
