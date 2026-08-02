from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from .repository import HistoryQuery, OwnerChannelScope
from .services import HistoryRequest, PreviewRequest
from .types import OperationCommand, OperationKind, TemplateInput, TemplateReference


_INVALID = "入力値が不正です。"


class ExactRequestSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({"request": [_INVALID]})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({"request": [_INVALID]})
        return super().to_internal_value(data)


class CanonicalUUIDField(serializers.Field):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            raise serializers.ValidationError(_INVALID)
        try:
            parsed = UUID(data)
        except (ValueError, TypeError, AttributeError):
            raise serializers.ValidationError(_INVALID) from None
        if str(parsed) != data:
            raise serializers.ValidationError(_INVALID)
        return parsed


class AwareDateTimeField(serializers.Field):
    def to_internal_value(self, data):
        if not isinstance(data, str) or not data or data != data.strip():
            raise serializers.ValidationError(_INVALID)
        parsed = parse_datetime(data)
        if parsed is None or timezone.is_naive(parsed):
            raise serializers.ValidationError(_INVALID)
        return parsed


class StrictTemplateField(serializers.Field):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError(_INVALID)
        result = {}
        errors = {}
        for field_name, value in data.items():
            if not isinstance(field_name, str) or not isinstance(value, dict):
                errors["request"] = serializers.ErrorDetail(_INVALID, code="invalid")
                continue
            nested = {}
            supplied = set(value)
            for key in {"displayName", "uri"} - supplied:
                nested[key] = serializers.ErrorDetail(_INVALID, code="required")
            if supplied - {"displayName", "uri"}:
                nested["request"] = serializers.ErrorDetail(_INVALID, code="invalid")
            for key in {"displayName", "uri"} & supplied:
                if not isinstance(value[key], str):
                    nested[key] = serializers.ErrorDetail(_INVALID, code="invalid")
            if nested:
                errors[field_name] = nested
                continue
            result[field_name] = dict(value)
        if errors:
            raise serializers.ValidationError(errors)
        return result


class PreviewRequestSerializer(ExactRequestSerializer):
    templateId = serializers.CharField(max_length=64, trim_whitespace=False)
    templateVersion = serializers.IntegerField(min_value=1)
    channelRevision = AwareDateTimeField()
    fields = StrictTemplateField()

    def to_command(self, channel_id: UUID) -> PreviewRequest:
        data = self.validated_data
        return PreviewRequest(
            channel_public_id=channel_id,
            expected_channel_revision=data["channelRevision"],
            template=TemplateInput(
                TemplateReference(data["templateId"], data["templateVersion"]),
                data["fields"],
            ),
        )


class OperationRequestSerializer(ExactRequestSerializer):
    kind = serializers.ChoiceField(choices=tuple(item.value for item in OperationKind))
    operationId = CanonicalUUIDField()
    channelRevision = AwareDateTimeField()
    confirmationToken = serializers.CharField(
        required=False, max_length=4096, trim_whitespace=False
    )
    templateId = serializers.CharField(
        required=False, max_length=64, trim_whitespace=False
    )
    templateVersion = serializers.IntegerField(required=False, min_value=1)
    fields = StrictTemplateField(required=False)
    subjectOperationId = CanonicalUUIDField(required=False)
    targetResourceId = CanonicalUUIDField(required=False)

    _VARIANT_FIELDS = {
        OperationKind.APPLY: {"confirmationToken", "templateId", "templateVersion", "fields"},
        OperationKind.UNLINK: {"targetResourceId"},
        OperationKind.RELEASE: {"targetResourceId"},
        OperationKind.RECHECK: {"subjectOperationId"},
        OperationKind.CLEANUP: {"subjectOperationId", "targetResourceId"},
    }

    def validate(self, attrs):
        kind = OperationKind(attrs["kind"])
        required = self._VARIANT_FIELDS[kind]
        base = {"kind", "operationId", "channelRevision"}
        supplied = set(self.initial_data)
        if supplied != base | required:
            raise serializers.ValidationError({"request": [_INVALID]})
        attrs["kind"] = kind
        return attrs

    def to_command(self, channel_id: UUID) -> OperationCommand:
        data = self.validated_data
        template = None
        if data["kind"] is OperationKind.APPLY:
            template = TemplateInput(
                TemplateReference(data["templateId"], data["templateVersion"]),
                data["fields"],
            )
        return OperationCommand(
            operation_id=data["operationId"],
            channel_public_id=channel_id,
            expected_channel_revision=data["channelRevision"],
            kind=data["kind"],
            subject_operation_id=data.get("subjectOperationId"),
            target_resource_id=data.get("targetResourceId"),
            confirmation_token=data.get("confirmationToken"),
            template=template,
        )


class HistoryQuerySerializer(ExactRequestSerializer):
    cursor = serializers.CharField(required=False, max_length=4096)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=50, default=20)

    def to_query(self, scope: OwnerChannelScope) -> HistoryQuery:
        return HistoryQuery(
            scope=scope,
            limit=self.validated_data["limit"],
            cursor=self.validated_data.get("cursor"),
        )

    def to_request(self, channel_id: UUID) -> HistoryRequest:
        return HistoryRequest(
            channel_public_id=channel_id,
            limit=self.validated_data["limit"],
            cursor=self.validated_data.get("cursor"),
        )
