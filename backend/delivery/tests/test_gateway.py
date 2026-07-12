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
        for status, failure in ((400, "invalid_request"), (401, "authentication"), (403, "permission"), (409, "conflict"), (429, "rate_limited"), (500, "service_unavailable"), (503, "service_unavailable"), (418, "unexpected")):
            with self.subTest(status=status):
                self.assertEqual(gateway._map_api_exception(FakeApiException(status, {})), LinePushRejected(failure))

        with patch("delivery.gateway.is_timeout_error", return_value=True):
            self.assertEqual(gateway._map_unexpected(TimeoutError("secret")), LinePushUnknown("timeout_unknown"))

    # テストケース: SDK呼出しがtimeoutまたは予期しない例外を送出する。
    # 期待値: 自動再試行せず1回の呼出しで、安全なunknownまたはunexpectedへ変換する。
    @override_settings(LINE_CHANNEL_ACCESS_TOKEN="secret-token", LINE_USER_ID="secret-user")
    def test_push_maps_runtime_errors_without_retry_or_sensitive_output(self):
        for error, expected in (
            (TimeoutError("raw timeout secret-token secret-user"), LinePushUnknown("timeout_unknown")),
            (RuntimeError("raw unexpected secret-token secret-user"), LinePushRejected("unexpected")),
        ):
            with self.subTest(error=type(error).__name__):
                api = Mock()
                api.push_message_with_http_info.side_effect = error

                result = LINEGateway(api_client_factory=lambda _: api).push_text(
                    LinePushCommand(uuid.uuid4(), "safe text")
                )

                self.assertEqual(result, expected)
                api.push_message_with_http_info.assert_called_once()
                self.assertEqual(
                    api.push_message_with_http_info.call_args.kwargs["_request_timeout"],
                    (3, 10),
                )
                self.assertNotIn("secret-token", repr(result))
                self.assertNotIn("secret-user", repr(result))
                self.assertNotIn("raw", repr(result))

    # テストケース: LINE SDK clientを標準factoryで構築する。
    # 期待値: access tokenを設定し、SDKの自動retryを0にしてMessagingApiへ渡す。
    @patch("delivery.gateway.MessagingApi")
    @patch("delivery.gateway.ApiClient")
    @patch("delivery.gateway.Configuration")
    def test_build_api_disables_sdk_retries(self, configuration_class, api_client_class, messaging_api_class):
        configuration = Mock()
        configuration_class.return_value = configuration
        api_client = Mock()
        api_client_class.return_value = api_client

        result = LINEGateway._build_api("token")

        configuration_class.assert_called_once_with(access_token="token")
        self.assertEqual(configuration.retries, 0)
        api_client_class.assert_called_once_with(configuration)
        messaging_api_class.assert_called_once_with(api_client)
        self.assertIs(result, messaging_api_class.return_value)

    # テストケース: access tokenまたは固定宛先の片方だけが欠けている。
    # 期待値: いずれもSDKを構築せずconfiguration failureになる。
    def test_each_missing_configuration_value_is_rejected(self):
        for token, user_id in (("", "user"), ("token", "")):
            with self.subTest(token=bool(token), user_id=bool(user_id)), override_settings(
                LINE_CHANNEL_ACCESS_TOKEN=token,
                LINE_USER_ID=user_id,
            ):
                factory = Mock()
                result = LINEGateway(api_client_factory=factory).push_text(
                    LinePushCommand(uuid.uuid4(), "text")
                )

                self.assertEqual(result, LinePushRejected("configuration"))
                factory.assert_not_called()


class FakeApiException(Exception):
    def __init__(self, status, headers):
        self.status = status
        self.headers = headers
