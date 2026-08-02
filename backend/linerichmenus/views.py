from __future__ import annotations

from collections.abc import Mapping

from rest_framework.response import Response
from rest_framework.exceptions import ParseError, UnsupportedMediaType, ValidationError

from lineaccounts.admin_authorization import OwnerOperationContext
from lineaccounts.authentication import OwnerPrincipal
from lineaccounts.views import OwnerProtectedAPIView

from .container import build_rich_menu_service
from .presenters import RichMenuPresenter
from .serializers import (
    HistoryQuerySerializer,
    OperationRequestSerializer,
    PreviewRequestSerializer,
)
from .services import (
    HistorySucceeded,
    OperationSucceeded,
    PreviewSucceeded,
    ServiceFailed,
    StateSucceeded,
    TemplateListSucceeded,
)
from .types import SafeResultCode
from .types import InputFieldError


_HTTP_STATUS = {
    SafeResultCode.INVALID_INPUT: 400,
    SafeResultCode.AUTHENTICATION_REQUIRED: 401,
    SafeResultCode.OWNER_OPERATION_BLOCKED: 403,
    SafeResultCode.CHANNEL_UNAVAILABLE: 404,
    SafeResultCode.STALE_CHANNEL: 409,
    SafeResultCode.OPERATION_CONFLICT: 409,
    SafeResultCode.OPERATION_IN_PROGRESS: 409,
    SafeResultCode.PREVIEW_EXPIRED: 409,
    SafeResultCode.TEMPLATE_CHANGED: 422,
    SafeResultCode.IMAGE_INVALID: 422,
    SafeResultCode.CHANNEL_INACTIVE: 422,
    SafeResultCode.LINE_REJECTED: 422,
    SafeResultCode.RATE_LIMITED: 429,
    SafeResultCode.INTEGRATION_NOT_READY: 503,
    SafeResultCode.TIMEOUT_UNKNOWN: 503,
    SafeResultCode.RESPONSE_UNKNOWN: 503,
    SafeResultCode.OBSERVATION_UNKNOWN: 503,
    SafeResultCode.STORAGE_RETRYABLE: 503,
    SafeResultCode.STORAGE_UNAVAILABLE: 503,
    SafeResultCode.UNEXPECTED: 500,
}

_SAFE_VALIDATION_FIELDS = frozenset(
    {
        "request", "templateId", "templateVersion", "channelRevision", "fields",
        "kind", "operationId", "confirmationToken", "subjectOperationId",
        "targetResourceId", "cursor", "limit",
    }
)


def _safe_validation_errors(detail) -> tuple[InputFieldError, ...]:
    collected = []

    def visit(value, path=()):
        if isinstance(value, Mapping):
            for key, child in value.items():
                if isinstance(key, str):
                    visit(child, path + (key,))
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                visit(child, path)
            return
        normalized = path[1:] if path[:1] == ("fields",) else path
        field = ".".join(normalized)
        safe = field in _SAFE_VALIDATION_FIELDS or (
            len(normalized) == 2
            and normalized[0] in {"area1", "area2", "area3"}
            and normalized[1] in {"displayName", "uri"}
        )
        if not safe:
            field = "request"
        code = getattr(value, "code", "invalid")
        reason = {
            "required": "required",
            "blank": "required",
            "max_length": "too_long",
        }.get(code, "invalid")
        error = InputFieldError(field, reason)
        if error not in collected:
            collected.append(error)

    visit(detail)
    return tuple(collected) or (InputFieldError("request", "invalid"),)


def _owner(request) -> OwnerOperationContext:
    principal = request.user
    assert isinstance(principal, OwnerPrincipal)
    return OwnerOperationContext(
        owner_session_id=principal.owner_session_id,
        identity_public_id=principal.identity_public_id,
    )


class RichMenuAPIView(OwnerProtectedAPIView):
    def service(self):
        return build_rich_menu_service()

    def presenter(self):
        return RichMenuPresenter()

    def handle_exception(self, exc):
        if isinstance(exc, (ValidationError, ParseError, UnsupportedMediaType)):
            failure = ServiceFailed(
                SafeResultCode.INVALID_INPUT,
                errors=_safe_validation_errors(getattr(exc, "detail", None)),
            )
            return Response(self.presenter().error(failure), status=400)
        return super().handle_exception(exc)

    def respond(self, result, expected, render, *, success_status=200):
        if isinstance(result, ServiceFailed):
            return Response(
                self.presenter().error(result),
                status=_HTTP_STATUS.get(result.code, 503),
            )
        if not isinstance(result, expected):
            failure = ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
            return Response(self.presenter().error(failure), status=503)
        return Response(render(result), status=success_status)


class TemplateListAPIView(RichMenuAPIView):
    def get(self, request):
        result = self.service().list_templates(_owner(request))
        return self.respond(
            result,
            TemplateListSucceeded,
            lambda value: self.presenter().templates(value.templates),
        )


class ChannelPreviewAPIView(RichMenuAPIView):
    def post(self, request, channel_id):
        serializer = PreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = self.service().preview(_owner(request), serializer.to_command(channel_id))
        return self.respond(result, PreviewSucceeded, self.presenter().preview)


class ChannelStateAPIView(RichMenuAPIView):
    def get(self, request, channel_id):
        result = self.service().get_state(_owner(request), channel_id)
        return self.respond(result, StateSucceeded, self.presenter().state)


class ChannelOperationAPIView(RichMenuAPIView):
    def post(self, request, channel_id):
        serializer = OperationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = self.service().start_operation(
            _owner(request), serializer.to_command(channel_id)
        )
        return self.respond(result, OperationSucceeded, self.presenter().operation)


class OperationDetailAPIView(RichMenuAPIView):
    def get(self, request, operation_id):
        result = self.service().get_operation(_owner(request), operation_id)
        return self.respond(result, OperationSucceeded, self.presenter().operation)


class ChannelHistoryAPIView(RichMenuAPIView):
    def get(self, request, channel_id):
        serializer = HistoryQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        result = self.service().list_history(
            _owner(request), serializer.to_request(channel_id)
        )
        return self.respond(result, HistorySucceeded, self.presenter().history)
