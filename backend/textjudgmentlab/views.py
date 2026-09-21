from __future__ import annotations

from hmac import compare_digest

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    MethodNotAllowed,
    NotAuthenticated,
    ParseError,
    PermissionDenied,
    Throttled,
    UnsupportedMediaType,
    ValidationError,
)
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import IsLabOwner, LabAccessError, LabBearerAuthentication
from .runtime import LabRuntimeConfigured


_MAX_BODY_BYTES = 32 * 1024
_MAX_AUTHORIZATION_BYTES = 8 * 1024
_ERRORS = {
    "reauthentication_required": (401, "LINEで再認証してください。"),
    "wrong_channel": (403, "対応するLINEミニアプリから開き直してください。"),
    "not_allowed": (403, "このラボは利用できません。"),
    "origin_rejected": (403, "対応する入口から開き直してください。"),
    "invalid_input": (400, "入力内容を確認してください。"),
    "input_too_large": (413, "入力が大きすぎます。"),
    "unsupported_media_type": (415, "JSON形式で送信してください。"),
    "method_not_allowed": (405, "この操作は利用できません。"),
    "rate_limited": (429, "時間をおいて再度お試しください。"),
    "access_unavailable": (503, "利用資格を確認できませんでした。"),
    "configuration_unavailable": (503, "ラボを利用できません。"),
    "unexpected": (500, "処理を完了できませんでした。"),
}


class LabBoundaryError(APIException):
    def __init__(self, code: str) -> None:
        self.public_code = code
        self.status_code = _ERRORS[code][0]
        super().__init__(detail="Lab request rejected", code=code)


class LabAPIView(APIView):
    authentication_classes = [LabBearerAuthentication]
    permission_classes = [IsLabOwner]
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        if request.method == "POST":
            runtime = settings.TEXT_JUDGMENT_LAB_RUNTIME
            if not isinstance(runtime, LabRuntimeConfigured):
                raise LabBoundaryError("configuration_unavailable")
            supplied = request.headers.get("Origin")
            if (
                not isinstance(supplied, str)
                or not supplied
                or supplied == "null"
                or "," in supplied
                or not compare_digest(supplied, runtime.origin)
            ):
                raise LabBoundaryError("origin_rejected")
            authorization = request.headers.get("Authorization", "")
            try:
                authorization_size = len(authorization.encode("ascii"))
            except (AttributeError, UnicodeEncodeError):
                authorization_size = _MAX_AUTHORIZATION_BYTES + 1
            if authorization_size > _MAX_AUTHORIZATION_BYTES:
                raise LabBoundaryError("reauthentication_required")
            if len(request._request.body) > _MAX_BODY_BYTES:
                raise LabBoundaryError("input_too_large")
        return super().initial(request, *args, **kwargs)

    def handle_exception(self, exc):
        if isinstance(exc, LabAccessError):
            return self._error(exc.public_code, exc.status_code)
        if isinstance(exc, LabBoundaryError):
            return self._error(exc.public_code, exc.status_code)
        if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            return self._error("reauthentication_required")
        if isinstance(exc, PermissionDenied):
            return self._error("not_allowed")
        if isinstance(exc, (ParseError, ValidationError)):
            return self._error("invalid_input")
        if isinstance(exc, UnsupportedMediaType):
            return self._error("unsupported_media_type")
        if isinstance(exc, MethodNotAllowed):
            return self._error("method_not_allowed")
        if isinstance(exc, Throttled):
            return self._error("rate_limited")
        return self._error("unexpected")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    @staticmethod
    def _error(code: str, status_code: int | None = None) -> Response:
        configured_status, message = _ERRORS.get(code, _ERRORS["unexpected"])
        return Response(
            {"error": {"code": code, "message": message}},
            status=status_code or configured_status,
        )
