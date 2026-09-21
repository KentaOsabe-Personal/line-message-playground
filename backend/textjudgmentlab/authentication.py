from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from asgiref.sync import async_to_sync
from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission

from .line_gateway import (
    LabLineGateway,
    LineIdentityRejected,
    LineIdentityUnavailable,
    VerifiedLabIdentity,
)
from .runtime import LabRuntime, LabRuntimeConfigured
from .types import LabPrincipal


_MAX_AUTHORIZATION_BYTES = 8 * 1024


class LabAccessError(APIException):
    status_code = 401
    default_detail = "Lab access rejected"
    default_code = "reauthentication_required"

    def __init__(self, public_code: str, status_code: int) -> None:
        self.public_code = public_code
        self.status_code = status_code
        super().__init__(detail=self.default_detail, code=public_code)


class LabBearerAuthentication(BaseAuthentication):
    def __init__(
        self,
        runtime: LabRuntime | None = None,
        gateway: LabLineGateway | None = None,
        *,
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._runtime = runtime
        self._gateway = gateway
        self._clock = clock

    def authenticate(self, request):
        runtime = self._runtime or settings.TEXT_JUDGMENT_LAB_RUNTIME
        if not isinstance(runtime, LabRuntimeConfigured):
            raise LabAccessError("configuration_unavailable", 503)

        authorization = request.headers.get("Authorization")
        token = self._bearer_token(authorization)
        if token is None:
            raise LabAccessError("reauthentication_required", 401)

        gateway = self._gateway or LabLineGateway(runtime.channel_id)
        result = async_to_sync(gateway.verify)(token)
        if isinstance(result, LineIdentityUnavailable):
            raise LabAccessError("access_unavailable", 503)
        if isinstance(result, LineIdentityRejected):
            status_code = 403 if result.code == "wrong_channel" else 401
            code = result.code if result.code == "wrong_channel" else "reauthentication_required"
            raise LabAccessError(code, status_code)
        if not isinstance(result, VerifiedLabIdentity) or result.expires_at <= self._clock():
            raise LabAccessError("reauthentication_required", 401)
        return LabPrincipal(result.expires_at, result.subject_digest), None

    def authenticate_header(self, request) -> str:
        return "Bearer"

    @staticmethod
    def _bearer_token(authorization: object) -> str | None:
        if not isinstance(authorization, str):
            return None
        try:
            encoded = authorization.encode("ascii")
        except UnicodeEncodeError:
            return None
        if len(encoded) > _MAX_AUTHORIZATION_BYTES or any(char in authorization for char in "\r\n\0"):
            return None
        parts = authorization.split(" ")
        if len(parts) != 2 or parts[0] != "Bearer" or not parts[1]:
            return None
        return parts[1]


class IsLabOwner(BasePermission):
    def __init__(
        self,
        runtime: LabRuntime | None = None,
        *,
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._runtime = runtime
        self._clock = clock

    def has_permission(self, request, view) -> bool:
        runtime = self._runtime or settings.TEXT_JUDGMENT_LAB_RUNTIME
        principal = request.user
        return (
            isinstance(runtime, LabRuntimeConfigured)
            and isinstance(principal, LabPrincipal)
            and principal.is_valid_at(self._clock())
            and runtime.owner_digest.matches(principal.owner_digest)
        )
