from datetime import UTC, datetime

import httpx
from unittest import IsolatedAsyncioTestCase

from textjudgmentlab.line_gateway import (
    LabLineGateway,
    LineIdentityRejected,
    LineIdentityUnavailable,
    VerifiedLabIdentity,
)


NOW = datetime(2026, 9, 21, tzinfo=UTC)


def gateway_for(handler):
    return LabLineGateway(
        "1234567890",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
    )


class LabLineGatewayTests(IsolatedAsyncioTestCase):
    # テストケース: 固定endpointへID tokenとserver設定channel IDを一回だけ送る
    # 期待値: profile情報を捨て、期限とsubject digestだけの本人情報を返す
    async def test_verifies_once_and_discards_raw_identity(self) -> None:
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(request.url, httpx.URL("https://api.line.me/oauth2/v2.1/verify"))
            self.assertEqual(
                request.content.decode(),
                "id_token=id-token-canary&client_id=1234567890",
            )
            self.assertEqual(
                request.extensions["timeout"],
                {"connect": 4.0, "read": 4.0, "write": 4.0, "pool": 4.0},
            )
            return httpx.Response(200, json={
                "iss": "https://access.line.me",
                "aud": "1234567890",
                "exp": NOW.timestamp() + 60,
                "sub": "Uowner-subject",
                "name": "捨てる表示名",
                "picture": "https://example.test/private.png",
            })

        result = await gateway_for(handler).verify("id-token-canary")

        self.assertIsInstance(result, VerifiedLabIdentity)
        self.assertEqual(len(requests), 1)
        assert isinstance(result, VerifiedLabIdentity)
        self.assertEqual(result.expires_at, datetime.fromtimestamp(NOW.timestamp() + 60, UTC))
        self.assertEqual(
            result.subject_digest,
            "5f12d926ce706e0d1f936fa1adb9dfb91f4d80a16937ad474d5376bd5e758a12",
        )
        self.assertNotIn("Uowner-subject", repr(result))
        self.assertNotIn("捨てる表示名", repr(result))

    # テストケース: audience違い、失効、不正subject、LINE障害を受け取る
    # 期待値: raw payloadを漏らさず固定分類へ縮約し、自動再試行しない
    async def test_classifies_rejections_and_unavailability_without_retry(self) -> None:
        cases = (
            ({"iss": "https://access.line.me", "aud": "other", "exp": NOW.timestamp() + 1, "sub": "U1"}, "wrong_channel"),
            ({"iss": "https://access.line.me", "aud": "1234567890", "exp": NOW.timestamp(), "sub": "U1"}, "invalid_proof"),
            ({"iss": "https://access.line.me", "aud": "1234567890", "exp": NOW.timestamp() + 1, "sub": ""}, "invalid_proof"),
        )
        for payload, code in cases:
            with self.subTest(code=code):
                result = await gateway_for(lambda _request: httpx.Response(200, json=payload)).verify("secret")
                self.assertEqual(result, LineIdentityRejected(code))
                self.assertNotIn("secret", repr(result))

        audience_error = await gateway_for(lambda _request: httpx.Response(400, json={
            "error": "invalid_request",
            "error_description": "Invalid IdToken Audience.",
        })).verify("secret")
        self.assertEqual(audience_error, LineIdentityRejected("wrong_channel"))

        attempts = 0

        def unavailable(request):
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("secret", request=request)

        self.assertEqual(await gateway_for(unavailable).verify("secret"), LineIdentityUnavailable())
        self.assertEqual(attempts, 1)
