from __future__ import annotations

from hmac import compare_digest

from asgiref.sync import async_to_sync
from django.conf import settings
from django.utils import timezone
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
from .container import build_judgment_service
from .runtime import LabRuntimeConfigured
from .serializers import JudgmentRequestSerializer
from .types import (
    JudgmentFailure,
    JudgmentSuccess,
    KnownEvidence,
)


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
    "judgment_failed": (502, "判定できませんでした。"),
    "judgment_timeout": (504, "判定できませんでした。"),
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
                raise LabBoundaryError("access_unavailable")
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


def _timestamp(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _evidence(value):
    if isinstance(value, KnownEvidence):
        candidate = value.value
        return {
            "kind": "known",
            "value": candidate.value if hasattr(candidate, "value") else candidate,
        }
    return {"kind": value.kind}


def _choice(value):
    return {
        "type": value.type,
        "choice": value.choice,
        "probabilities": dict(value.probabilities),
        "confidence": value.confidence,
    }


def _success_payload(result: JudgmentSuccess) -> dict[str, object]:
    evidence = result.evidence
    details = result.details
    return {
        "contractVersion": result.contract_version,
        "consultationId": str(result.consultation_id),
        "requestId": str(result.request_id),
        "revision": result.revision,
        "model": result.model,
        "evidence": {
            "topic": _evidence(evidence.topic),
            "relevance": evidence.relevance.value,
            "change": evidence.change.value,
            "scope": _evidence(evidence.scope),
            "workaround": _evidence(evidence.workaround),
            "result": _evidence(evidence.result),
            "impact": evidence.impact,
            "urgency": _evidence(evidence.urgency),
        },
        "details": {
            "choices": {key: _choice(value) for key, value in details.choices.items()},
            "score": {
                "type": details.score.type,
                "score": details.score.score,
                "legend": dict(details.score.legend),
                "probabilities": dict(details.score.probabilities),
                "confidence": details.score.confidence,
            },
            "noul": {"type": details.noul.type, "noul": details.noul.noul},
            "jevElapsedMs": details.jev_elapsed_ms,
        },
    }


class LabAccessAPIView(LabAPIView):
    def post(self, request):
        if not isinstance(request.data, dict) or request.data:
            raise ValidationError("invalid access body")
        return Response({
            "status": "authorized",
            "expiresAt": _timestamp(request.user.expires_at),
            "serverTime": _timestamp(timezone.now()),
        })


class LabJudgmentAPIView(LabAPIView):
    def post(self, request):
        serializer = JudgmentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = async_to_sync(build_judgment_service().evaluate)(
            request.user,
            serializer.to_request(),
        )
        if isinstance(result, JudgmentSuccess):
            return Response(_success_payload(result))
        if not isinstance(result, JudgmentFailure):
            return self._error("unexpected")
        mapping = {
            "invalid_request": "invalid_input",
            "rate_limited": "rate_limited",
            "access_expired": "reauthentication_required",
            "judge_unavailable": "judgment_failed",
            "judge_timeout": "judgment_timeout",
            "configuration_unavailable": "access_unavailable",
            "unexpected": "unexpected",
        }
        return self._error(mapping[result.code])
