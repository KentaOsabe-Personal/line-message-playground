from collections.abc import Mapping

from rest_framework import serializers

from .types import IdToken, UserAccessToken


_SAFE_FIELD_ERROR = "入力値が不正です。"


class StrictCharField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if not isinstance(data, bool):
            self.fail("invalid")
        return data


class SensitiveCharField(StrictCharField):
    def __init__(self, *args, value_type, **kwargs):
        self.value_type = value_type
        super().__init__(*args, **kwargs)

    def run_validation(self, data=serializers.empty):
        return self.value_type(super().run_validation(data))


class StrictRequestSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({"non_field_errors": [_SAFE_FIELD_ERROR]})
        unknown_fields = set(data) - set(self.fields)
        if unknown_fields:
            raise serializers.ValidationError(
                {field: [_SAFE_FIELD_ERROR] for field in sorted(unknown_fields)}
            )
        return super().to_internal_value(data)


def _sensitive_string_field(*, required: bool = True) -> StrictCharField:
    return StrictCharField(
        allow_blank=False,
        max_length=16 * 1024,
        required=required,
        trim_whitespace=False,
        write_only=True,
    )


class LineLoginRequestSerializer(StrictRequestSerializer):
    idToken = SensitiveCharField(
        value_type=IdToken,
        allow_blank=False,
        max_length=16 * 1024,
        trim_whitespace=False,
        write_only=True,
    )


class RecipientRegistrationRequestSerializer(StrictRequestSerializer):
    channelId = serializers.UUIDField()
    accessToken = SensitiveCharField(
        value_type=UserAccessToken,
        allow_blank=False,
        max_length=16 * 1024,
        required=False,
        trim_whitespace=False,
        write_only=True,
    )


class RecipientStateRequestSerializer(StrictRequestSerializer):
    enabled = StrictBooleanField()


class UnlinkRequestSerializer(StrictRequestSerializer):
    confirmationToken = _sensitive_string_field(required=False)
    userAccessToken = SensitiveCharField(
        value_type=UserAccessToken,
        allow_blank=False,
        max_length=16 * 1024,
        required=False,
        trim_whitespace=False,
        write_only=True,
    )
