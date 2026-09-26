from collections.abc import Mapping
from uuid import UUID

from rest_framework import serializers

from .types import (
    ConfirmedAnswers,
    Impact,
    JudgmentContext,
    JudgmentRequest,
    QuestionId,
    Scope,
    Topic,
    Workaround,
)


_SAFE_ERROR = "入力値が不正です。"


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({"non_field_errors": [_SAFE_ERROR]})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {key: [_SAFE_ERROR] for key in sorted(unknown)}
            )
        return super().to_internal_value(data)


class StrictChoiceField(serializers.ChoiceField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid_choice", input=data)
        return super().to_internal_value(data)


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if not isinstance(data, bool):
            self.fail("invalid")
        return data


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if not isinstance(data, int) or isinstance(data, bool):
            self.fail("invalid")
        return super().to_internal_value(data)


class CanonicalUuidField(serializers.Field):
    default_error_messages = {"invalid": _SAFE_ERROR}

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        try:
            value = UUID(data)
        except (ValueError, AttributeError, TypeError):
            self.fail("invalid")
        if str(value) != data:
            self.fail("invalid")
        return value

    def to_representation(self, value):
        return str(value)


class CodePointTextField(serializers.Field):
    default_error_messages = {"invalid": _SAFE_ERROR}

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        value = data.strip()
        if not value or len(value) > 1000:
            self.fail("invalid")
        return value

    def to_representation(self, value):
        return value


class ConfirmedAnswersSerializer(StrictSerializer):
    topic = StrictChoiceField(choices=[value.value for value in Topic], allow_null=True)
    scope = StrictChoiceField(choices=[value.value for value in Scope], allow_null=True)
    workaround = StrictChoiceField(
        choices=[value.value for value in Workaround], allow_null=True
    )
    urgency = StrictBooleanField(allow_null=True)


class JudgmentContextSerializer(StrictSerializer):
    question = StrictChoiceField(choices=[value.value for value in QuestionId])
    confirmed = ConfirmedAnswersSerializer()
    recentUserTexts = serializers.ListField(
        child=CodePointTextField(), allow_empty=True, max_length=2
    )
    impact = StrictChoiceField(choices=[value.value for value in Impact])

    def validate(self, attrs):
        confirmed = attrs["confirmed"]
        topic = confirmed["topic"]
        question = attrs["question"]
        if topic == Topic.NOTIFICATION_SETTINGS.value and confirmed["workaround"] is not None:
            raise serializers.ValidationError({"non_field_errors": [_SAFE_ERROR]})
        if question not in (QuestionId.START.value, QuestionId.TOPIC.value) and topic is None:
            raise serializers.ValidationError({"non_field_errors": [_SAFE_ERROR]})
        if question == QuestionId.WORKAROUND.value and topic != Topic.MISSING_NOTIFICATION.value:
            raise serializers.ValidationError({"non_field_errors": [_SAFE_ERROR]})
        if question == QuestionId.RESULT.value and confirmed["scope"] is None:
            raise serializers.ValidationError({"non_field_errors": [_SAFE_ERROR]})
        return attrs


class JudgmentRequestSerializer(StrictSerializer):
    contractVersion = StrictIntegerField(min_value=1, max_value=1)
    consultationId = CanonicalUuidField()
    requestId = CanonicalUuidField()
    revision = StrictIntegerField(min_value=0)
    text = CodePointTextField()
    context = JudgmentContextSerializer()

    def to_request(self) -> JudgmentRequest:
        if not hasattr(self, "validated_data"):
            raise AssertionError("is_valid() must be called before to_request()")
        data = self.validated_data
        context = data["context"]
        confirmed = context["confirmed"]
        return JudgmentRequest(
            contract_version=1,
            consultation_id=data["consultationId"],
            request_id=data["requestId"],
            revision=data["revision"],
            text=data["text"],
            context=JudgmentContext(
                question=QuestionId(context["question"]),
                confirmed=ConfirmedAnswers(
                    topic=Topic(confirmed["topic"]) if confirmed["topic"] else None,
                    scope=Scope(confirmed["scope"]) if confirmed["scope"] else None,
                    workaround=(
                        Workaround(confirmed["workaround"])
                        if confirmed["workaround"]
                        else None
                    ),
                    urgency=confirmed["urgency"],
                ),
                recent_user_texts=tuple(context["recentUserTexts"]),
                impact=Impact(context["impact"]),
            ),
        )
