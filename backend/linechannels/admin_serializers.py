from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from .admin_types import (
    DeleteAdminChannel,
    RegisterAdminChannel,
    SetAdminChannelState,
    UpdateAdminChannel,
)
from .types import AccessToken, ChannelSecret, CredentialPair
from .validators import (
    BoundaryValidationError,
    validate_bot_user_id,
    validate_label,
    validate_messaging_api_channel_id,
    validate_provider_id,
)


_INVALID = "入力値が不正です。"
_MAX_CREDENTIAL_BYTES = 16 * 1024


class ExactRequestSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({"request": [_INVALID]})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {field: [_INVALID] for field in sorted(unknown)}
            )
        return super().to_internal_value(data)


class AwareDateTimeField(serializers.Field):
    def to_internal_value(self, data):
        if not isinstance(data, str) or not data or data != data.strip():
            raise serializers.ValidationError(_INVALID)
        parsed = parse_datetime(data)
        if parsed is None or timezone.is_naive(parsed):
            raise serializers.ValidationError(_INVALID)
        return parsed

    def to_representation(self, value: datetime):
        return value.isoformat()


def _boundary_validator(validator):
    def validate(value):
        try:
            return validator(value)
        except BoundaryValidationError:
            raise serializers.ValidationError(_INVALID) from None

    return validate


def _validate_credential(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > _MAX_CREDENTIAL_BYTES
    ):
        raise serializers.ValidationError(_INVALID)
    return value


class CredentialPairSerializerMixin(ExactRequestSerializer):
    accessToken = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )
    channelSecret = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )

    def _validate_optional_pair(self, attrs):
        access_present = "accessToken" in attrs
        secret_present = "channelSecret" in attrs
        access = attrs.get("accessToken")
        secret = attrs.get("channelSecret")
        if not access_present and not secret_present:
            attrs["credentialPair"] = None
            return attrs
        if access == "" and secret == "":
            attrs["credentialPair"] = None
            return attrs
        if not access_present or not secret_present or not access or not secret:
            raise serializers.ValidationError({"credentialPair": [_INVALID]})
        try:
            attrs["credentialPair"] = CredentialPair(
                AccessToken(_validate_credential(access)),
                ChannelSecret(_validate_credential(secret)),
            )
        except serializers.ValidationError:
            raise serializers.ValidationError({"credentialPair": [_INVALID]}) from None
        return attrs

    def credential_pair(self) -> CredentialPair | None:
        if not hasattr(self, "validated_data"):
            raise AssertionError("serializer must be validated")
        return self.validated_data.get("credentialPair")


class CreateChannelRequestSerializer(ExactRequestSerializer):
    label = serializers.CharField(
        trim_whitespace=False,
        validators=[_boundary_validator(validate_label)],
    )
    messagingApiChannelId = serializers.CharField(
        validators=[_boundary_validator(validate_messaging_api_channel_id)]
    )
    botUserId = serializers.CharField(
        validators=[_boundary_validator(validate_bot_user_id)]
    )
    providerId = serializers.CharField(
        validators=[_boundary_validator(validate_provider_id)]
    )
    accessToken = serializers.CharField(write_only=True, trim_whitespace=False)
    channelSecret = serializers.CharField(write_only=True, trim_whitespace=False)
    active = serializers.BooleanField()

    def validate_accessToken(self, value):
        return _validate_credential(value)

    def validate_channelSecret(self, value):
        return _validate_credential(value)

    def to_command(self) -> RegisterAdminChannel:
        data = self.validated_data
        return RegisterAdminChannel(
            messaging_api_channel_id=data["messagingApiChannelId"],
            bot_user_id=data["botUserId"],
            label=data["label"],
            provider_id=data["providerId"],
            credentials=CredentialPair(
                AccessToken(data["accessToken"]),
                ChannelSecret(data["channelSecret"]),
            ),
            is_active=data["active"],
        )


class UpdateChannelRequestSerializer(CredentialPairSerializerMixin):
    expectedUpdatedAt = AwareDateTimeField()
    label = serializers.CharField(
        required=False,
        trim_whitespace=False,
        validators=[_boundary_validator(validate_label)],
    )
    messagingApiChannelId = serializers.CharField(
        required=False,
        validators=[_boundary_validator(validate_messaging_api_channel_id)],
    )
    botUserId = serializers.CharField(
        required=False,
        validators=[_boundary_validator(validate_bot_user_id)],
    )
    providerId = serializers.CharField(
        required=False,
        validators=[_boundary_validator(validate_provider_id)],
    )

    def validate(self, attrs):
        raw_channel_id = self.context.get("channel_id")
        if isinstance(raw_channel_id, str):
            try:
                parsed = UUID(raw_channel_id)
            except ValueError:
                raise serializers.ValidationError({"channelId": [_INVALID]}) from None
            if str(parsed) != raw_channel_id:
                raise serializers.ValidationError({"channelId": [_INVALID]})
        attrs = self._validate_optional_pair(attrs)
        changed = any(
            field in attrs
            for field in ("label", "messagingApiChannelId", "botUserId", "providerId")
        ) or attrs["credentialPair"] is not None
        if not changed:
            raise serializers.ValidationError({"request": [_INVALID]})
        return attrs

    def to_command(self, channel_id: UUID) -> UpdateAdminChannel:
        data = self.validated_data
        return UpdateAdminChannel(
            channel_public_id=channel_id,
            expected_updated_at=data["expectedUpdatedAt"],
            messaging_api_channel_id=data.get("messagingApiChannelId"),
            bot_user_id=data.get("botUserId"),
            label=data.get("label"),
            provider_id=data.get("providerId"),
            credentials=data["credentialPair"],
        )


class SetChannelStateRequestSerializer(CredentialPairSerializerMixin):
    expectedUpdatedAt = AwareDateTimeField()
    active = serializers.BooleanField()

    def validate(self, attrs):
        return self._validate_optional_pair(attrs)

    def to_command(self, channel_id: UUID) -> SetAdminChannelState:
        data = self.validated_data
        return SetAdminChannelState(
            channel_public_id=channel_id,
            expected_updated_at=data["expectedUpdatedAt"],
            is_active=data["active"],
            repair_credentials=data["credentialPair"],
        )


class DeleteChannelRequestSerializer(ExactRequestSerializer):
    expectedUpdatedAt = AwareDateTimeField()

    def to_command(self, channel_id: UUID) -> DeleteAdminChannel:
        return DeleteAdminChannel(channel_id, self.validated_data["expectedUpdatedAt"])


class ConnectionCheckRequestSerializer(ExactRequestSerializer):
    pass
