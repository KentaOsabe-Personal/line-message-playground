from rest_framework import serializers


class StrictCharField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class PreviewRequestSerializer(serializers.Serializer):
    subject = StrictCharField(allow_blank=True, trim_whitespace=False)
    body = StrictCharField(allow_blank=True, trim_whitespace=False)


class SendDeliveryRequestSerializer(PreviewRequestSerializer):
    operationId = serializers.UUIDField()
    confirmationToken = StrictCharField(allow_blank=False)
