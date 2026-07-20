from __future__ import annotations

from hmac import compare_digest

from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware

from .errors import SafeAPIError


_SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS", "TRACE"))


def enforce_exact_origin_and_csrf(request) -> None:
    if request.method in _SAFE_METHODS:
        return
    origins = settings.CSRF_TRUSTED_ORIGINS
    supplied = request.META.get("HTTP_ORIGIN")
    if (
        len(origins) != 1
        or not isinstance(supplied, str)
        or not supplied
        or supplied == "null"
        or "," in supplied
        or not compare_digest(supplied, origins[0])
    ):
        raise SafeAPIError("csrf_failed")

    middleware = CsrfViewMiddleware(lambda _request: None)
    failure = middleware.process_view(request, lambda _request: None, (), {})
    if failure is not None:
        raise SafeAPIError("csrf_failed")


class ExactOriginCsrfMixin:
    def initial(self, request, *args, **kwargs):
        enforce_exact_origin_and_csrf(request._request)
        return super().initial(request, *args, **kwargs)
