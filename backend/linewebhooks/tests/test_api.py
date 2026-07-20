from unittest.mock import Mock, patch

from django.test import Client, SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from linewebhooks.types import IngressAccepted, IngressRejected
from linewebhooks.views import WebhookAPIView


class WebhookAPIViewTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.service = Mock()
        self.view = WebhookAPIView.as_view()

    def _request(self, method: str = "post"):
        return self.factory.generic(
            method.upper(),
            "/ignored/",
            b'{"raw":"bytes"}',
            content_type="application/json",
            HTTP_X_LINE_SIGNATURE="signature-value",
        )

    # テストケース: owner session や parser なしで匿名 POST を送る
    # 期待値: path候補、署名、未加工本文を一度だけ service へ渡し、空200を返す
    def test_post_passes_raw_body_to_service_and_returns_empty_200(self) -> None:
        self.service.ingest.return_value = IngressAccepted()

        with patch.object(WebhookAPIView, "service_factory", return_value=self.service):
            response = self.view(self._request(), channel_public_key="public-key")
            response.render()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.service.ingest.assert_called_once_with(
            "public-key",
            b'{"raw":"bytes"}',
            "signature-value",
        )

    # テストケース: app-localのchannel別URLへCSRF tokenやowner sessionなしでPOSTする
    # 期待値: auth・permission・parserを起動せず、URL dispatcher経由でserviceへ到達する
    @override_settings(ROOT_URLCONF="linewebhooks.urls")
    def test_app_local_route_accepts_anonymous_post_without_parser_or_csrf(self) -> None:
        client = Client(enforce_csrf_checks=True)
        self.service.ingest.return_value = IngressAccepted()

        with patch.object(WebhookAPIView, "service_factory", return_value=self.service):
            response = client.post(
                "/public-key/",
                data=b'{"raw":"bytes"}',
                content_type="application/json",
                HTTP_X_LINE_SIGNATURE="signature-value",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WebhookAPIView.authentication_classes, [])
        self.assertEqual(WebhookAPIView.permission_classes, [])
        self.assertEqual(WebhookAPIView.parser_classes, [])
        self.service.ingest.assert_called_once_with(
            "public-key",
            b'{"raw":"bytes"}',
            "signature-value",
        )

    # テストケース: raw bodyの読取り回数を記録するrequestをViewへ渡す
    # 期待値: request dataへ触れずbodyを正確に一度だけ取得する
    def test_reads_raw_body_exactly_once(self) -> None:
        class CountingRequest:
            headers = {"X-Line-Signature": "signature-value"}

            def __init__(self) -> None:
                self.body_reads = 0

            @property
            def body(self) -> bytes:
                self.body_reads += 1
                return b"raw-body"

            @property
            def data(self) -> object:
                raise AssertionError("request.data must not be accessed")

        request = CountingRequest()
        self.service.ingest.return_value = IngressAccepted()
        view = WebhookAPIView()
        view.service_factory = lambda: self.service

        response = view.post(request, channel_public_key="public-key")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.body_reads, 1)

    # テストケース: service の安全な拒否分類を HTTP 応答へ変換する
    # 期待値: payloadは400、signatureは401、channelは404、保存と予期外は503へ固定写像する
    def test_maps_safe_service_results_to_fixed_http_responses(self) -> None:
        cases = (
            (IngressRejected("payload_rejected"), 400, "webhook_rejected"),
            (IngressRejected("signature_rejected"), 401, "webhook_rejected"),
            (IngressRejected("channel_unavailable"), 404, "webhook_rejected"),
            (IngressRejected("storage_unavailable"), 503, "webhook_unavailable"),
            (IngressRejected("unexpected"), 503, "webhook_unavailable"),
        )
        for result, expected_status, expected_code in cases:
            with self.subTest(result=result):
                self.service.ingest.return_value = result
                with patch.object(
                    WebhookAPIView,
                    "service_factory",
                    return_value=self.service,
                ):
                    response = self.view(
                        self._request(),
                        channel_public_key="public-key",
                    )
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.data, {"error": {"code": expected_code}})

    # テストケース: Webhook URLへPOST以外のHTTPメソッドを送る
    # 期待値: serviceを呼ばず、すべて同じ固定405応答になる
    def test_non_post_methods_return_fixed_405_without_service_call(self) -> None:
        for method in ("get", "put", "delete", "options"):
            with self.subTest(method=method), patch.object(
                WebhookAPIView,
                "service_factory",
                return_value=self.service,
            ):
                response = self.view(
                    self._request(method),
                    channel_public_key="public-key",
                )
                self.assertEqual(response.status_code, 405)
                self.assertEqual(
                    response.data,
                    {"error": {"code": "method_not_allowed"}},
                )
        self.service.ingest.assert_not_called()

    # テストケース: service境界が予期しない例外を送出する
    # 期待値: 生例外を公開せず、固定503応答へ置換する
    def test_unexpected_service_exception_is_hidden(self) -> None:
        self.service.ingest.side_effect = RuntimeError("exception-canary")

        with patch.object(WebhookAPIView, "service_factory", return_value=self.service):
            response = self.view(self._request(), channel_public_key="public-key")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.data,
            {"error": {"code": "webhook_unavailable"}},
        )
        self.assertNotIn("exception-canary", str(response.data))
