from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from textjudgmentlab.authentication import (
    IsLabOwner,
    LabAccessError,
    LabBearerAuthentication,
)
from textjudgmentlab.line_gateway import (
    LineIdentityRejected,
    VerifiedLabIdentity,
)
from textjudgmentlab.runtime import LabRuntimeConfigured, OwnerDigest, SecretValue
from textjudgmentlab.types import LabPrincipal


NOW = datetime(2026, 9, 21, tzinfo=UTC)


def runtime(owner_digest: str = "a" * 64) -> LabRuntimeConfigured:
    return LabRuntimeConfigured(
        channel_id="1234567890",
        owner_digest=OwnerDigest(owner_digest),
        origin="https://lab.example.test",
        api_key=SecretValue("api-key"),
        model="jev-1.13.0",
    )


class StubGateway:
    def __init__(self, result) -> None:
        self.result = result
        self.tokens = []

    async def verify(self, token: str):
        self.tokens.append(token)
        return self.result


class LabBearerAuthenticationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()

    # テストケース: 正しいBearer tokenをLINE検証し、owner sessionと無関係なprincipalを作る
    # 期待値: raw tokenをprincipalへ保持せず、permissionがdigestと期限を定数時間比較する
    def test_builds_dedicated_principal_and_permission_checks_digest_and_expiry(self) -> None:
        gateway = StubGateway(VerifiedLabIdentity(NOW + timedelta(minutes=5), "a" * 64))
        authentication = LabBearerAuthentication(
            runtime=runtime(), gateway=gateway, clock=lambda: NOW,
        )
        request = self.factory.post("/lab", {}, HTTP_AUTHORIZATION="Bearer raw-token-canary")

        principal, auth_context = authentication.authenticate(request)

        self.assertIsInstance(principal, LabPrincipal)
        self.assertEqual(gateway.tokens, ["raw-token-canary"])
        self.assertIsNone(auth_context)
        self.assertNotIn("raw-token-canary", repr(principal))
        request.user = principal
        self.assertTrue(IsLabOwner(runtime=runtime(), clock=lambda: NOW).has_permission(request, object()))
        self.assertFalse(IsLabOwner(runtime=runtime("b" * 64), clock=lambda: NOW).has_permission(request, object()))
        self.assertFalse(IsLabOwner(runtime=runtime(), clock=lambda: NOW + timedelta(minutes=6)).has_permission(request, object()))

    # テストケース: 証明なし、不正形式、誤チャネルのtokenで保護操作を試みる
    # 期待値: 固定codeだけを返し、cookieやJev結果を認証根拠にしない
    def test_rejects_missing_malformed_and_wrong_channel_credentials(self) -> None:
        authentication = LabBearerAuthentication(
            runtime=runtime(), gateway=StubGateway(LineIdentityRejected("wrong_channel")), clock=lambda: NOW,
        )
        for authorization, code in (
            (None, "reauthentication_required"),
            ("Basic abc", "reauthentication_required"),
            ("Bearer ", "reauthentication_required"),
            ("Bearer abc def", "reauthentication_required"),
            ("Bearer token", "wrong_channel"),
        ):
            headers = {} if authorization is None else {"HTTP_AUTHORIZATION": authorization}
            request = self.factory.post("/lab", {}, **headers)
            with self.subTest(authorization=authorization), self.assertRaises(LabAccessError) as raised:
                authentication.authenticate(request)
            self.assertEqual(raised.exception.public_code, code)

        cookie_only = self.factory.post("/lab", {}, HTTP_COOKIE="sessionid=owner-cookie")
        with self.assertRaises(LabAccessError) as raised:
            authentication.authenticate(cookie_only)
        self.assertEqual(raised.exception.public_code, "reauthentication_required")
