import re
from collections.abc import Mapping

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    ParseError,
    PermissionDenied,
    Throttled,
    UnsupportedMediaType,
    ValidationError,
)
from rest_framework.response import Response


_SAFE_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z", re.ASCII)
_SAFE_FIELD_MESSAGE = "入力値が不正です。"
_ERRORS = {
    "authentication_required": (status.HTTP_401_UNAUTHORIZED, "LINEで再認証してください。"),
    "invalid_line_proof": (status.HTTP_401_UNAUTHORIZED, "LINEで再認証してください。"),
    "owner_not_allowed": (status.HTTP_403_FORBIDDEN, "この操作は許可されていません。"),
    "owner_operation_blocked": (status.HTTP_403_FORBIDDEN, "この操作は現在利用できません。"),
    "csrf_failed": (status.HTTP_403_FORBIDDEN, "ページを再読み込みしてください。"),
    "validation_error": (status.HTTP_400_BAD_REQUEST, "入力内容を確認してください。"),
    "channel_not_found": (status.HTTP_404_NOT_FOUND, "対象を確認できませんでした。"),
    "recipient_not_found": (status.HTTP_404_NOT_FOUND, "対象を確認できませんでした。"),
    "stale_confirmation": (status.HTTP_409_CONFLICT, "もう一度内容を確認してください。"),
    "unlink_in_progress": (status.HTTP_409_CONFLICT, "連携解除を処理中です。"),
    "unlink_attempt_stale": (status.HTTP_409_CONFLICT, "連携状態を再確認してください。"),
    "provider_mismatch": (status.HTTP_422_UNPROCESSABLE_ENTITY, "チャネル設定を確認してください。"),
    "channel_unavailable": (status.HTTP_422_UNPROCESSABLE_ENTITY, "チャネルを利用できません。"),
    "line_rate_limited": (status.HTTP_429_TOO_MANY_REQUESTS, "時間をおいて再試行してください。"),
    "line_unavailable": (status.HTTP_503_SERVICE_UNAVAILABLE, "LINEへ接続できませんでした。"),
    "storage_unavailable": (status.HTTP_503_SERVICE_UNAVAILABLE, "処理を完了できませんでした。"),
    "unexpected": (status.HTTP_500_INTERNAL_SERVER_ERROR, "処理を完了できませんでした。"),
}


class SafeAPIError(Exception):
    def __init__(self, code: str) -> None:
        if code not in _ERRORS:
            raise ValueError("unknown safe error code")
        self.code = code
        super().__init__(code)


def _error_response(code: str, *, fields: dict[str, list[str]] | None = None) -> Response:
    http_status, summary = _ERRORS[code]
    error: dict[str, object] = {"code": code, "summary": summary}
    if fields:
        error["fields"] = fields
    return Response({"error": error}, status=http_status)


def _safe_validation_fields(detail: object) -> dict[str, list[str]] | None:
    if not isinstance(detail, Mapping):
        return None
    fields = {
        field: [_SAFE_FIELD_MESSAGE]
        for field in detail
        if isinstance(field, str) and _SAFE_FIELD_NAME.fullmatch(field)
    }
    return fields or None


def safe_exception_handler(exc: Exception, context: dict[str, object]) -> Response:
    if isinstance(exc, SafeAPIError):
        return _error_response(exc.code)
    if isinstance(exc, ValidationError):
        return _error_response(
            "validation_error",
            fields=_safe_validation_fields(exc.detail),
        )
    if isinstance(exc, (ParseError, UnsupportedMediaType)):
        return _error_response("validation_error")
    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        return _error_response("authentication_required")
    if isinstance(exc, (PermissionDenied, DjangoPermissionDenied)):
        return _error_response("owner_not_allowed")
    if isinstance(exc, (NotFound, Http404)):
        return _error_response("recipient_not_found")
    if isinstance(exc, Throttled):
        return _error_response("line_rate_limited")
    return _error_response("unexpected")
