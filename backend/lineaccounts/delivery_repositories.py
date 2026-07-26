import hashlib
from datetime import UTC, datetime
from uuid import UUID

from django.db import models

from delivery.types import (
    DeliveryChannelChoice,
    DeliveryRecipientChoice,
    LinkedTargetSnapshot,
    LiveDeliveryTarget,
    OwnerIdentitySnapshot,
    TargetRevision,
    TargetUnavailable,
)
from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerAccount
from lineaccounts.types import LineSubject
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

    def list_recipients(
        self,
        owner_identity_id: UUID,
        channel_id: UUID,
    ) -> tuple[DeliveryRecipientChoice, ...] | TargetUnavailable:
        owner_identity = (
            LineIdentity.objects.filter(
                public_id=owner_identity_id,
                owner_account__state=OwnerAccount.State.ACTIVE,
                owner_account__identity_id=models.F("pk"),
            )
            .values("pk", "provider_id")
            .first()
        )
        if owner_identity is None:
            return TargetUnavailable()

        channel = (
            LineChannel.objects.filter(
                public_id=channel_id,
                provider_id=owner_identity["provider_id"],
            )
            .values("pk", "is_active")
            .first()
        )
        if channel is None:
            return TargetUnavailable()

        recipients = tuple(
            DeliveryRecipient.objects.filter(
                identity_id=owner_identity["pk"],
                identity__public_id=owner_identity_id,
                identity__owner_account__state=OwnerAccount.State.ACTIVE,
                identity__owner_account__identity_id=models.F("identity_id"),
                line_channel_id=channel["pk"],
                line_channel__public_id=channel_id,
                line_channel__provider_id=models.F("identity__provider_id"),
            )
            .select_related("identity", "line_channel")
            .only(
                "public_id",
                "enabled",
                "friendship_state",
                "identity__display_name",
                "line_channel__public_id",
                "line_channel__is_active",
            )
            .order_by("public_id")
        )
        if not recipients:
            return TargetUnavailable("no_deliverable_recipient")

        return tuple(
            DeliveryRecipientChoice(
                recipient_public_id=recipient.public_id,
                display_name=recipient.identity.display_name,
                enabled=recipient.enabled,
                friendship_state=recipient.friendship_state,
                available=(
                    recipient.line_channel.is_active
                    and recipient.enabled
                    and recipient.friendship_state
                    == DeliveryRecipient.FriendshipState.FRIEND
                ),
                unavailable_reason=_recipient_unavailable_reason(
                    channel_active=recipient.line_channel.is_active,
                    recipient_enabled=recipient.enabled,
                    friendship_state=recipient.friendship_state,
                ),
            )
            for recipient in recipients
        )

    def resolve(
        self,
        owner_identity_id: UUID,
        channel_id: UUID,
        recipient_id: UUID,
    ) -> LiveDeliveryTarget | TargetUnavailable:
        target = (
            DeliveryRecipient.objects.filter(
                public_id=recipient_id,
                identity__public_id=owner_identity_id,
                identity__owner_account__state=OwnerAccount.State.ACTIVE,
                identity__owner_account__identity_id=models.F("identity_id"),
                line_channel__public_id=channel_id,
                line_channel__provider_id=models.F("identity__provider_id"),
            )
            .values(
                "identity__public_id",
                "identity__provider_id",
                "identity__subject",
                "line_channel__public_id",
                "line_channel__label",
                "line_channel__is_active",
                "line_channel__updated_at",
                "public_id",
                "enabled",
                "friendship_state",
                "updated_at",
            )
            .first()
        )
        if target is None:
            return TargetUnavailable()

        snapshot = LinkedTargetSnapshot(
            channel_public_id=target["line_channel__public_id"],
            channel_label=target["line_channel__label"],
            recipient_public_id=target["public_id"],
            channel_active=target["line_channel__is_active"],
            recipient_enabled=target["enabled"],
            friendship_state=target["friendship_state"],
        )
        revision = build_target_revision(
            owner_identity_public_id=target["identity__public_id"],
            channel_public_id=target["line_channel__public_id"],
            provider_id=target["identity__provider_id"],
            channel_active=target["line_channel__is_active"],
            channel_updated_at=target["line_channel__updated_at"],
            recipient_public_id=target["public_id"],
            recipient_enabled=target["enabled"],
            friendship_state=target["friendship_state"],
            recipient_updated_at=target["updated_at"],
        )
        return LiveDeliveryTarget(
            owner_identity=OwnerIdentitySnapshot(target["identity__public_id"]),
            provider_id=target["identity__provider_id"],
            snapshot=snapshot,
            revision=revision,
            subject=LineSubject(target["identity__subject"]),
            delivery_available=(
                snapshot.channel_active
                and snapshot.recipient_enabled
                and snapshot.friendship_state
                == DeliveryRecipient.FriendshipState.FRIEND
            ),
        )


def _recipient_unavailable_reason(
    *,
    channel_active: bool,
    recipient_enabled: bool,
    friendship_state: str,
) -> str | None:
    if not channel_active:
        return "channel_inactive"
    if not recipient_enabled:
        return "recipient_disabled"
    if friendship_state == DeliveryRecipient.FriendshipState.NOT_FRIEND:
        return "not_friend"
    if friendship_state == DeliveryRecipient.FriendshipState.UNKNOWN:
        return "friendship_unknown"
    return None


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
