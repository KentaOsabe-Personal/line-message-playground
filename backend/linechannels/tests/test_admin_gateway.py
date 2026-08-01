from unittest.mock import Mock

from django.test import SimpleTestCase

from linechannels.admin_gateway import DefaultLineBotInfoGateway
from linechannels.types import AccessToken


class SafeApiError(Exception):
    def __init__(self, status):
        self.status = status

    def __str__(self):
        raise AssertionError("SDK例外を文字列化してはならない")


class RecordingFactory:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def __call__(self, access_token, *, retries):
        self.calls.append((access_token, retries))
        return self.client


class LineBotInfoGatewayTests(SimpleTestCase):
    # テストケース: bot identityをbounded timeoutで一回だけ取得する
    # 期待値: retry 0、one-shot、bot user IDだけのsafe結果を返してclientを閉じる
    def test_get_bot_identity_is_one_shot_bounded_and_retry_free(self):
        client = Mock()
        client.get_bot_info.return_value = Mock(user_id="U" + "a" * 32)
        factory = RecordingFactory(client)
        gateway = DefaultLineBotInfoGateway(factory, timeout_seconds=3.0)

        result = gateway.get_bot_identity(AccessToken("gateway-token"))

        self.assertEqual(result.bot_user_id, "U" + "a" * 32)
        self.assertEqual(factory.calls, [("gateway-token", 0)])
        client.get_bot_info.assert_called_once_with(_request_timeout=3.0)
        client.close.assert_called_once_with()
        self.assertNotIn("gateway-token", repr(result))

    # テストケース: LINEが401、403、429、5xx、timeout、不定形応答を返す
    # 期待値: 例外やbodyを文字列化せず固定3分類へ収束する
    def test_external_failures_are_safely_classified(self):
        cases = (
            (SafeApiError(401), "authentication_failed"),
            (SafeApiError(403), "authentication_failed"),
            (SafeApiError(429), "rate_limited"),
            (SafeApiError(503), "line_unavailable"),
            (TimeoutError("raw-canary"), "line_unavailable"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                client = Mock()
                client.get_bot_info.side_effect = error
                result = DefaultLineBotInfoGateway(
                    RecordingFactory(client)
                ).get_bot_identity(AccessToken("gateway-token"))
                self.assertEqual(result.code, expected)
                self.assertNotIn("raw-canary", repr(result))

        client = Mock()
        client.get_bot_info.return_value = Mock(user_id="unexpected")
        malformed = DefaultLineBotInfoGateway(
            RecordingFactory(client)
        ).get_bot_identity(AccessToken("gateway-token"))
        self.assertEqual(malformed.code, "line_unavailable")
