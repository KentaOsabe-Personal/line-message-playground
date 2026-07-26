import hashlib
from datetime import UTC, datetime
from uuid import UUID

from django.db import models

from delivery.types import DeliveryChannelChoice, TargetRevision
from lineaccounts.models import LineIdentity, OwnerAccount
from linechannels.models import LineChannel


_FRIENDSHIP_STATES = frozenset(("friend", "not_friend", "unknown"))


class DeliveryTargetDirectory:
    def list_channels(
        self,
        owner_identity_id: UUID,
    ) -> tuple[DeliveryChannelChoice, ...]:
        owner_provider = LineIdentity.objects.filter(
            public_id=owner_identity_id,
            owner_account__state=OwnerAccount.State.ACTIVE,
            owner_account__identity_id=models.F("pk"),
        ).values("provider_id")[:1]
        channels = (
            LineChannel.objects.filter(provider_id=models.Subquery(owner_provider))
            .only("public_id", "label", "is_active")
            .order_by("public_id")
        )
        return tuple(
            DeliveryChannelChoice(
                channel_public_id=channel.public_id,
                label=channel.label,
                active=channel.is_active,
                available=channel.is_active,
                unavailable_reason=(
                    None if channel.is_active else "channel_inactive"
                ),
            )
            for channel in channels
        )


def build_target_revision(
    *,
    owner_identity_public_id: UUID,
    channel_public_id: UUID,
    provider_id: str,
    channel_active: bool,
    channel_updated_at: datetime,
    recipient_public_id: UUID,
    recipient_enabled: bool,
    friendship_state: str,
    recipient_updated_at: datetime,
) -> TargetRevision:
    if not isinstance(owner_identity_public_id, UUID):
        raise ValueError("invalid owner identity public ID")
    if not isinstance(channel_public_id, UUID):
        raise ValueError("invalid channel public ID")
    if not isinstance(provider_id, str) or not provider_id:
        raise ValueError("invalid provider ID")
    if type(channel_active) is not bool:
        raise ValueError("invalid channel active state")
    if not isinstance(recipient_public_id, UUID):
        raise ValueError("invalid recipient public ID")
    if type(recipient_enabled) is not bool:
        raise ValueError("invalid recipient enabled state")
    if friendship_state not in _FRIENDSHIP_STATES:
        raise ValueError("invalid friendship state")

    canonical_parts = (
        "v1",
        str(owner_identity_public_id),
        str(channel_public_id),
        provider_id,
        "1" if channel_active else "0",
        _canonical_datetime(channel_updated_at),
        str(recipient_public_id),
        "1" if recipient_enabled else "0",
        friendship_state,
        _canonical_datetime(recipient_updated_at),
    )
    canonical = b"".join(
        len(encoded).to_bytes(4, "big") + encoded
        for part in canonical_parts
        for encoded in (part.encode("utf-8"),)
    )
    return TargetRevision(hashlib.sha256(canonical).hexdigest())


def _canonical_datetime(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("target revision datetime must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
