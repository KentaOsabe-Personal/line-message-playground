import uuid
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from linebot.v3.messaging import (
    ButtonsTemplate,
    PostbackAction,
    TemplateMessage,
    TextMessage,
)

from delivery.gateway import (
    ChannelPushGateway,
    LINEChannelPushGateway,
    LINEGateway,
    LinePushAccepted,
    LinePushCommand,
    LinePushRejected,
    LinePushUnknown,
)
from delivery.types import (
    LinePushAccepted as LinkedLinePushAccepted,
    LinePushUnknown as LinkedLinePushUnknown,
    PushLinkedRecipientCommand,
    ReceiptCapability,
)
from lineaccounts.types import LineSubject
from linechannels.types import AccessToken


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


class LINEChannelPushGatewayTests(SimpleTestCase):
    operation_id = uuid.UUID("12345678-1234-5678-1234-567812345678")

    def _command(self, receipt_capability=None):
        return PushLinkedRecipientCommand(
            operation_id=self.operation_id,
            access_token=AccessToken("selected-token-canary"),
            subject=LineSubject("selected-subject-canary"),
            text="整形済み本文",
            receipt_capability=receipt_capability,
        )

    # テストケース: 受取確認なしの選択済みcommandをpushする。
    # 期待値: 選択tokenとsubjectだけを使い、Text一件を一回だけ送る。
    def test_push_without_receipt_builds_one_recipient_one_message_request(self):
        api = Mock()
        api.push_message_with_http_info.return_value = (
            None,
            200,
            {"X-Line-Request-Id": "request-id"},
        )
        factory = Mock(return_value=api)

        gateway: ChannelPushGateway = LINEChannelPushGateway(
            api_client_factory=factory
        )
        result = gateway.push(self._command())

        self.assertEqual(
            result,
            LinkedLinePushAccepted("request-id", None),
        )
        factory.assert_called_once_with("selected-token-canary")
        api.push_message_with_http_info.assert_called_once()
        kwargs = api.push_message_with_http_info.call_args.kwargs
        request = kwargs["push_message_request"]
        self.assertEqual(request.to, "selected-subject-canary")
        self.assertEqual(len(request.messages), 1)
        self.assertIsInstance(request.messages[0], TextMessage)
        self.assertEqual(request.messages[0].text, "整形済み本文")
        self.assertEqual(kwargs["x_line_retry_key"], str(self.operation_id))
        self.assertEqual(kwargs["_request_timeout"], (3, 10))

    # テストケース: 受取確認capability付きcommandをpushする。
    # 期待値: Textの後へ固定文言Buttonsと一つのpostback actionを同じrequestで送る。
    def test_push_with_receipt_adds_one_buttons_template_to_same_request(self):
        capability = ReceiptCapability("opaque-capability-canary")
        api = Mock()
        api.push_message_with_http_info.return_value = (
            None,
            200,
            {"X-Line-Request-Id": "request-id"},
        )

        result = LINEChannelPushGateway(
            api_client_factory=lambda _: api
        ).push(self._command(capability))

        self.assertEqual(
            result,
            LinkedLinePushAccepted("request-id", None),
        )
        api.push_message_with_http_info.assert_called_once()
        messages = api.push_message_with_http_info.call_args.kwargs[
            "push_message_request"
        ].messages
        self.assertEqual(len(messages), 2)
        self.assertIsInstance(messages[0], TextMessage)
        self.assertIsInstance(messages[1], TemplateMessage)
        self.assertEqual(messages[1].alt_text, "受取確認")
        self.assertIsInstance(messages[1].template, ButtonsTemplate)
        self.assertEqual(
            messages[1].template.text,
            "受け取り後にボタンを押してください。",
        )
        self.assertEqual(len(messages[1].template.actions), 1)
        action = messages[1].template.actions[0]
        self.assertIsInstance(action, PostbackAction)
        self.assertEqual(action.label, "受け取りました")
        self.assertEqual(
            action.data,
            "v1:delivery.received:opaque-capability-canary",
        )

    # テストケース: secret wrapperとcommandを文字列化する。
    # 期待値: token、subject、capabilityの生値をreprへ露出しない。
    def test_command_and_secrets_remain_redacted_outside_request_boundary(self):
        command = self._command(
            ReceiptCapability("opaque-capability-canary")
        )

        rendered = " ".join(
            (
                repr(command),
                repr(command.access_token),
                repr(command.subject),
                repr(command.receipt_capability),
            )
        )

        for canary in (
            "selected-token-canary",
            "selected-subject-canary",
            "opaque-capability-canary",
        ):
            self.assertNotIn(canary, rendered)

    # テストケース: 選択channel用SDK clientを標準factoryで構築する。
    # 期待値: 渡されたtokenを設定し、SDK自動retryを0にする。
    @patch("delivery.gateway.MessagingApi")
    @patch("delivery.gateway.ApiClient")
    @patch("delivery.gateway.Configuration")
    def test_build_api_disables_sdk_retries(
        self,
        configuration_class,
        api_client_class,
        messaging_api_class,
    ):
        configuration = Mock()
        configuration_class.return_value = configuration
        api_client = Mock()
        api_client_class.return_value = api_client

        result = LINEChannelPushGateway._build_api("selected-token")

        configuration_class.assert_called_once_with(
            access_token="selected-token"
        )
        self.assertEqual(configuration.retries, 0)
        api_client_class.assert_called_once_with(configuration)
        messaging_api_class.assert_called_once_with(api_client)
        self.assertIs(result, messaging_api_class.return_value)

    # テストケース: SDKが503またはconnection errorを送出する。
    # 期待値: 固定宛先mapperを通さずlinked用の閉じたunknownへ安全に縮約する。
    def test_errors_return_linked_unknown_without_legacy_mapper(self):
        for error in (
            FakeApiException(503, {}),
            ConnectionError("raw connection detail"),
        ):
            with self.subTest(error=type(error).__name__):
                api = Mock()
                api.push_message_with_http_info.side_effect = error
                gateway = LINEChannelPushGateway(
                    api_client_factory=lambda _: api
                )

                with patch.object(
                    LINEGateway,
                    "_map_api_exception",
                ) as api_mapper, patch.object(
                    LINEGateway,
                    "_map_unexpected",
                ) as unexpected_mapper:
                    result = gateway.push(self._command())

                self.assertEqual(
                    result,
                    LinkedLinePushUnknown("response_unknown"),
                )
                api_mapper.assert_not_called()
                unexpected_mapper.assert_not_called()
                api.push_message_with_http_info.assert_called_once()


class FakeApiException(Exception):
    def __init__(self, status, headers):
        self.status = status
        self.headers = headers
