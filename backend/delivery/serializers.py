from collections.abc import Mapping
from uuid import UUID

from rest_framework import serializers


_SAFE_FIELD_ERROR = "入力値が不正です。"


class StrictCharField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


# fixed配信APIが後続のroute切替まで使用する既存request契約。
class PreviewRequestSerializer(serializers.Serializer):
    subject = StrictCharField(allow_blank=True, trim_whitespace=False)
    body = StrictCharField(allow_blank=True, trim_whitespace=False)


class SendDeliveryRequestSerializer(PreviewRequestSerializer):
    operationId = serializers.UUIDField()
    confirmationToken = StrictCharField(allow_blank=False)


class CanonicalUUIDField(serializers.UUIDField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid", value=data)
        try:
            value = UUID(data)
        except (AttributeError, TypeError, ValueError):
            self.fail("invalid", value=data)
        if str(value) != data:
            self.fail("invalid", value=data)
        return value


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if not isinstance(data, bool):
            self.fail("invalid", input=data)
        return data


class StrictRequestSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError(
                {"non_field_errors": [_SAFE_FIELD_ERROR]}
            )
        unknown_fields = set(data) - set(self.fields)
        if unknown_fields:
            raise serializers.ValidationError(
                {"non_field_errors": [_SAFE_FIELD_ERROR]}
            )
        return super().to_internal_value(data)


class LinkedPreviewRequestSerializer(StrictRequestSerializer):
    channelId = CanonicalUUIDField()
    recipientId = CanonicalUUIDField()
    subject = StrictCharField(
        allow_blank=True,
        max_length=16 * 1024,
        trim_whitespace=False,
    )
    body = StrictCharField(
        allow_blank=True,
        max_length=16 * 1024,
        trim_whitespace=False,
    )
    receiptRequested = StrictBooleanField()


class LinkedSendDeliveryRequestSerializer(LinkedPreviewRequestSerializer):
    operationId = CanonicalUUIDField()
    confirmationToken = StrictCharField(
        allow_blank=False,
        max_length=16 * 1024,
        trim_whitespace=False,
        write_only=True,
    )


class DeliveryChannelChoiceResponseSerializer(serializers.Serializer):
    channelId = serializers.UUIDField(
        source="channel_public_id",
        read_only=True,
    )
    label = serializers.CharField(read_only=True)
    active = serializers.BooleanField(read_only=True)
    deliveryAvailable = serializers.BooleanField(
        source="available",
        read_only=True,
    )
    unavailableReason = serializers.ChoiceField(
        source="unavailable_reason",
        choices=("channel_inactive",),
        allow_null=True,
        read_only=True,
    )


class DeliveryRecipientChoiceResponseSerializer(serializers.Serializer):
    recipientId = serializers.UUIDField(
        source="recipient_public_id",
        read_only=True,
    )
    displayName = serializers.CharField(source="display_name", read_only=True)
    enabled = serializers.BooleanField(read_only=True)
    friendshipState = serializers.ChoiceField(
        source="friendship_state",
        choices=("friend", "not_friend", "unknown"),
        read_only=True,
    )
    deliveryAvailable = serializers.BooleanField(
        source="available",
        read_only=True,
    )
    unavailableReason = serializers.ChoiceField(
        source="unavailable_reason",
        choices=(
            "channel_inactive",
            "recipient_disabled",
            "not_friend",
            "friendship_unknown",
        ),
        allow_null=True,
        read_only=True,
    )
