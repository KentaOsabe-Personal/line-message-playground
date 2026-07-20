from collections.abc import Callable

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import WebhookIngressService
from .types import IngressAccepted, IngressRejected


def _build_service() -> WebhookIngressService:
    from .container import build_webhook_ingress_service

    return build_webhook_ingress_service()


class WebhookAPIView(APIView):
    authentication_classes = []
    permission_classes = []
    parser_classes = []
    service_factory: Callable[[], WebhookIngressService] = staticmethod(_build_service)

    def post(self, request: object, channel_public_key: str) -> Response:
        try:
            raw_body = request.body  # type: ignore[attr-defined]
            signature = request.headers.get("X-Line-Signature")  # type: ignore[attr-defined]
            result = self.service_factory().ingest(
                channel_public_key,
                raw_body,
                signature,
            )
        except Exception:
            return self._unavailable()

        if isinstance(result, IngressAccepted):
            return Response(status=status.HTTP_200_OK)
        if not isinstance(result, IngressRejected):
            return self._unavailable()
        if result.code == "payload_rejected":
            return self._rejected(status.HTTP_400_BAD_REQUEST)
        if result.code == "signature_rejected":
            return self._rejected(status.HTTP_401_UNAUTHORIZED)
        if result.code == "channel_unavailable":
            return self._rejected(status.HTTP_404_NOT_FOUND)
        return self._unavailable()

    def options(self, request: object, *args: object, **kwargs: object) -> Response:
        return self.http_method_not_allowed(request, *args, **kwargs)

    def http_method_not_allowed(
        self,
        request: object,
        *args: object,
        **kwargs: object,
    ) -> Response:
        return Response(
            {"error": {"code": "method_not_allowed"}},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @staticmethod
    def _rejected(http_status: int) -> Response:
        return Response(
            {"error": {"code": "webhook_rejected"}},
            status=http_status,
        )

    @staticmethod
    def _unavailable() -> Response:
        return Response(
            {"error": {"code": "webhook_unavailable"}},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
