import uuid
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from delivery.gateway import (
    LINEGateway,
    LinePushAccepted,
    LinePushCommand,
    LinePushRejected,
    LinePushUnknown,
)


@override_settings(LINE_MESSAGE_DELIVERY_CORE_ENABLED=True)
class LINEGatewayTests(SimpleTestCase):
    # テストケース: 認証情報または固定宛先が欠けた状態でpushする。
    # 期待値: SDKを構築せず安全なconfiguration failureを返す。
    @override_settings(LINE_CHANNEL_ACCESS_TOKEN="", LINE_USER_ID="")
    def test_missing_configuration_is_rejected_before_sdk_call(self):
        factory = Mock()
        result = LINEGateway(api_client_factory=factory).push_text(
            LinePushCommand(uuid.uuid4(), "text")
        )

        self.assertEqual(result, LinePushRejected("configuration"))
        factory.assert_not_called()

    # テストケース: SDKが大文字小文字の異なるrequest ID header付きで成功する。
    # 期待値: テキスト1件とretry keyで1回呼び出し、request IDを返す。
    @override_settings(LINE_CHANNEL_ACCESS_TOKEN="token", LINE_USER_ID="user")
    def test_success_calls_sdk_once_and_extracts_request_id(self):
        api = Mock()
        api.push_message_with_http_info.return_value = (None, 200, {"x-line-request-id": "req"})
        result = LINEGateway(api_client_factory=lambda _: api).push_text(
            LinePushCommand(uuid.UUID("12345678-1234-5678-1234-567812345678"), "hello")
        )

        self.assertEqual(result, LinePushAccepted("req", None))
        api.push_message_with_http_info.assert_called_once()
        kwargs = api.push_message_with_http_info.call_args.kwargs
        self.assertEqual(kwargs["x_line_retry_key"], "12345678-1234-5678-1234-567812345678")
        self.assertEqual(kwargs["push_message_request"].to, "user")
        self.assertEqual(kwargs["push_message_request"].messages[0].text, "hello")

    # テストケース: 409既受理、HTTP拒否、timeoutをSDKが返す。
    # 期待値: raw bodyを使わず、成功・閉じた失敗種別・結果不明へ変換する。
    @override_settings(LINE_CHANNEL_ACCESS_TOKEN="token", LINE_USER_ID="user")
    def test_maps_accepted_conflict_http_errors_and_timeout(self):
        gateway = LINEGateway(api_client_factory=lambda _: Mock())

        accepted = gateway._map_api_exception(
            FakeApiException(409, {"X-Line-Accepted-Request-Id": "accepted"})
        )
        self.assertEqual(accepted, LinePushAccepted(None, "accepted"))
        for status, failure in ((400, "invalid_request"), (401, "authentication"), (403, "permission"), (409, "conflict"), (429, "rate_limited"), (500, "service_unavailable")):
            with self.subTest(status=status):
                self.assertEqual(gateway._map_api_exception(FakeApiException(status, {})), LinePushRejected(failure))

        with patch("delivery.gateway.is_timeout_error", return_value=True):
            self.assertEqual(gateway._map_unexpected(TimeoutError("secret")), LinePushUnknown("timeout_unknown"))


class FakeApiException(Exception):
    def __init__(self, status, headers):
        self.status = status
        self.headers = headers
