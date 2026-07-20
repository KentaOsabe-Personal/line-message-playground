from unittest.mock import Mock, patch

from django.test import Client, SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory

from linechannels.types import CredentialUnavailable
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.tests.support import (
    CHANNEL_ID,
    EVENT_IDS,
    RecordingHandler,
    build_service,
    event,
    signed_payload,
)
from linewebhooks.types import HandlerFailed, ReceiptStorageFailed
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


class PublicWebhookHTTPIntegrationTests(TestCase):
    def _post(
        self,
        service: object,
        raw_body: bytes,
        signature: str | None,
        *,
        channel_key: str | None = None,
    ):
        request_headers = (
            {"HTTP_X_LINE_SIGNATURE": signature}
            if signature is not None
            else {}
        )
        with patch.object(WebhookAPIView, "service_factory", return_value=service):
            return self.client.post(
                f"/api/line/webhooks/{channel_key or CHANNEL_ID}/",
                data=raw_body,
                content_type="application/json",
                **request_headers,
            )

    # テストケース: exact公開routeへowner sessionとCSRFなしで署名済みPOSTを送る
    # 期待値: parserへ依存せず具象serviceがraw bodyを検証し、空bodyの200とprocessed receiptを返す
    def test_exact_route_accepts_anonymous_signed_post(self) -> None:
        handler = RecordingHandler()
        service, _ = build_service(handler=handler)
        raw_body, signature = signed_payload([event(EVENT_IDS[0])])

        response = self._post(service, raw_body, signature)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(WebhookEventReceipt.objects.get().status, "processed")
        self.assertEqual(len(handler.events), 1)
        self.assertEqual(WebhookAPIView.authentication_classes, [])
        self.assertEqual(WebhookAPIView.permission_classes, [])
        self.assertEqual(WebhookAPIView.parser_classes, [])

    # テストケース: exact公開routeへGET・PUT・DELETE・OPTIONSを送る
    # 期待値: serviceを構築も呼出しもせず、全methodが同じ固定405になる
    def test_exact_route_rejects_every_non_post_method_without_service(self) -> None:
        factory = Mock()
        for method in ("get", "put", "delete", "options"):
            with self.subTest(method=method), patch.object(
                WebhookAPIView, "service_factory", factory
            ):
                response = getattr(self.client, method)(
                    f"/api/line/webhooks/{CHANNEL_ID}/"
                )
                self.assertEqual(response.status_code, 405)
                self.assertEqual(
                    response.json(), {"error": {"code": "method_not_allowed"}}
                )
        factory.assert_not_called()

    # テストケース: channel・signature・destination・payload・storage失敗を公開routeへ送る
    # 期待値: 固定404・401・400・503へ写像し、全rejectionでreceiptとhandlerを作らない
    def test_rejections_map_to_fixed_statuses_without_side_effects(self) -> None:
        handler = RecordingHandler()
        service, _ = build_service(handler=handler)
        valid_body, valid_signature = signed_payload([event(EVENT_IDS[0])])
        wrong_destination, wrong_destination_signature = signed_payload(
            [event(EVENT_IDS[0])], destination="U" + "9" * 32
        )
        too_many, too_many_signature = signed_payload(
            [event(EVENT_IDS[0]) for _ in range(11)]
        )
        cases = (
            ("not-a-uuid", valid_body, valid_signature, 404),
            (str(CHANNEL_ID), valid_body, None, 401),
            (str(CHANNEL_ID), valid_body, "invalid-signature", 401),
            (
                str(CHANNEL_ID),
                wrong_destination,
                wrong_destination_signature,
                400,
            ),
            (str(CHANNEL_ID), too_many, too_many_signature, 400),
        )
        for channel_key, body, signature, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                response = self._post(
                    service, body, signature, channel_key=channel_key
                )
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(
                    response.json(), {"error": {"code": "webhook_rejected"}}
                )

        class UnavailableCredentialRepository:
            def __init__(self, code: str) -> None:
                self.code = code

            def get(self, channel_public_id: object) -> CredentialUnavailable:
                return CredentialUnavailable(self.code)  # type: ignore[arg-type]

        for code in (
            "channel_not_found",
            "channel_inactive",
            "credential_unreadable",
        ):
            with self.subTest(credential_code=code):
                channel_service, _ = build_service(
                    handler=handler,
                    credential_repository=UnavailableCredentialRepository(code),
                )
                response = self._post(
                    channel_service, valid_body, valid_signature
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    response.json(), {"error": {"code": "webhook_rejected"}}
                )

        class UnavailableRepository:
            def accept_batch(self, candidates: object) -> ReceiptStorageFailed:
                return ReceiptStorageFailed()

        unavailable_service, _ = build_service(
            handler=handler,
            receipt_repository=UnavailableRepository(),
        )
        unavailable = self._post(
            unavailable_service, valid_body, valid_signature
        )
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json(), {"error": {"code": "webhook_unavailable"}}
        )
        self.assertEqual(WebhookEventReceipt.objects.count(), 0)
        self.assertEqual(handler.events, [])

    # テストケース: empty・duplicate・unsupported・handler failedを公開routeで順に処理する
    # 期待値: すべて空200となり、duplicateとunsupportedはhandlerを呼ばずfailed状態も再実行しない
    def test_valid_terminal_paths_all_return_empty_200(self) -> None:
        failed_handler = RecordingHandler(HandlerFailed())
        service, _ = build_service(handler=failed_handler)
        empty_body, empty_signature = signed_payload([])
        failed_body, failed_signature = signed_payload([event(EVENT_IDS[0])])
        unsupported_body, unsupported_signature = signed_payload(
            [event(EVENT_IDS[1], event_type="future-event")]
        )

        responses = [
            self._post(service, empty_body, empty_signature),
            self._post(service, failed_body, failed_signature),
            self._post(service, failed_body, failed_signature),
            self._post(service, unsupported_body, unsupported_signature),
        ]

        self.assertTrue(
            all(response.status_code == 200 for response in responses)
        )
        self.assertTrue(all(response.content == b"" for response in responses))
        self.assertEqual(len(failed_handler.events), 1)
        self.assertEqual(
            list(
                WebhookEventReceipt.objects.order_by("webhook_event_id").values_list(
                    "status", flat=True
                )
            ),
            ["failed", "unsupported"],
        )
