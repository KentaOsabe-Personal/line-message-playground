from rest_framework import serializers

from .types import JudgmentRequest


class JudgmentRequestSerializer(serializers.Serializer):
    contractVersion = serializers.IntegerField()
    text = serializers.CharField(max_length=1000, trim_whitespace=True, allow_blank=False)

    def to_internal_value(self, data):
        if (
            not isinstance(data, dict)
            or set(data) != {"contractVersion", "text"}
            or type(data["contractVersion"]) is not int
            or data["contractVersion"] != 3
            or not isinstance(data["text"], str)
        ):
            raise serializers.ValidationError({"non_field_errors": ["入力形式が不正です。"]})
        return super().to_internal_value(data)

    def to_request(self) -> JudgmentRequest:
        return JudgmentRequest(text=self.validated_data["text"])
