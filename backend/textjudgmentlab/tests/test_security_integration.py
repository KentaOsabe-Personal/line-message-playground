import asyncio
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from textjudgmentlab.line_gateway import (
    LabLineGateway,
    LineIdentityRejected,
    LineIdentityUnavailable,
    VerifiedLabIdentity,
)
from textjudgmentlab.runtime import LabRuntimeConfigured, OwnerDigest, SecretValue

RUNTIME = LabRuntimeConfigured(
    channel_id="1234567890",
    owner_digest=OwnerDigest("a" * 64),
    origin="https://lab.example.test",
    api_key=SecretValue("api-key"),
    model="jev-1.13.0",
)
FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


class _Gateway:
    def __init__(self, result) -> None:
        self.result = result
        self.tokens: list[str] = []

    async def verify(self, token: str):
        self.tokens.append(token)
        return self.result


@override_settings(TEXT_JUDGMENT_LAB_RUNTIME=RUNTIME)
class LabSecurityIntegrationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = APIClient(enforce_csrf_checks=True)
        self.url = "/api/labs/text-judgment/access"

    def _post(self, **headers):
        return self.client.post(
            self.url,
            {},
            format="json",
            HTTP_ORIGIN=RUNTIME.origin,
            **headers,
        )

    # テストケース: Bearer証明を実endpointへ送りLINE検証mockが本人identityを返す。
    # 期待値: profileやowner sessionを要求せず認可し、cookieを一切生成しない。
    def test_authenticates_through_line_gateway_without_owner_session_or_profile(self) -> None:
        gateway = _Gateway(VerifiedLabIdentity(FUTURE, "a" * 64))
        with patch("textjudgmentlab.authentication.LabLineGateway", return_value=gateway):
            response = self._post(HTTP_AUTHORIZATION="Bearer lab-token-canary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(gateway.tokens, ["lab-token-canary"])
        self.assertEqual(response.cookies, {})
        self.assertNotIn("profile", response.data)

    # テストケース: 誤チャネル、本人digest不一致、LINE照会障害を実endpointへ返す。
    # 期待値: 403/503のラボ専用codeへ分離し、秘密やowner schemaを返さない。
    def test_maps_line_identity_and_owner_failures_at_api_boundary(self) -> None:
        cases = (
            (LineIdentityRejected("wrong_channel"), 403, "wrong_channel"),
            (VerifiedLabIdentity(FUTURE, "b" * 64), 403, "not_allowed"),
            (LineIdentityUnavailable(), 503, "access_unavailable"),
        )
        for result, status_code, code in cases:
            with self.subTest(code=code):
                with patch(
                    "textjudgmentlab.authentication.LabLineGateway", return_value=_Gateway(result)
                ):
                    response = self._post(HTTP_AUTHORIZATION="Bearer secret-canary")
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.data["error"]["code"], code)
                self.assertEqual(set(response.data["error"]), {"code", "message"})
                self.assertNotIn("secret-canary", response.content.decode())

    # テストケース: owner cookieだけでラボ、ラボBearerだけでowner管理APIへ入る。
    # 期待値: 両方向とも拒否され、認証方式と公開error schemaが混在しない。
    def test_separates_lab_bearer_and_owner_cookie_in_both_directions(self) -> None:
        self.client.cookies["sessionid"] = "owner-cookie-canary"
        lab_response = self._post()

        self.client.cookies.clear()
        owner_response = self.client.get(
            "/api/account/channels/",
            HTTP_AUTHORIZATION="Bearer lab-token-canary",
        )

        self.assertEqual(lab_response.status_code, 401)
        self.assertEqual(lab_response.data["error"]["code"], "reauthentication_required")
        self.assertEqual(owner_response.status_code, 401)
        self.assertNotEqual(owner_response.data["error"].get("code"), "reauthentication_required")

    # テストケース: Origin、media type、methodを実access endpointで違反する。
    # 期待値: CSRF tokenに依存せずラボ専用codeとno-storeで拒否する。
    def test_enforces_origin_media_type_method_and_no_store_on_real_endpoint(self) -> None:
        gateway = _Gateway(VerifiedLabIdentity(FUTURE, "a" * 64))
        with patch("textjudgmentlab.authentication.LabLineGateway", return_value=gateway):
            requests = (
                self.client.post(self.url, {}, format="json", HTTP_AUTHORIZATION="Bearer x"),
                self.client.generic(
                    "POST",
                    self.url,
                    b"x",
                    content_type="text/plain",
                    HTTP_ORIGIN=RUNTIME.origin,
                    HTTP_AUTHORIZATION="Bearer x",
                ),
                self.client.get(
                    self.url, HTTP_ORIGIN=RUNTIME.origin, HTTP_AUTHORIZATION="Bearer x"
                ),
            )
        expected = (
            (403, "origin_rejected"),
            (415, "unsupported_media_type"),
            (405, "method_not_allowed"),
        )
        for response, (status_code, code) in zip(requests, expected, strict=True):
            self.assertEqual(response.status_code, status_code)
            self.assertEqual(response.data["error"]["code"], code)
            self.assertEqual(response["Cache-Control"], "no-store")

    # テストケース: 実LabLineGatewayへ不正issuer応答と4秒全体deadline超過を返す。
    # 期待値: 実endpointが再認証要求と照会障害へ安全に分類する。
    def test_integrates_line_issuer_validation_and_total_deadline(self) -> None:
        def invalid_issuer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "iss": "https://attacker.invalid",
                    "aud": RUNTIME.channel_id,
                    "exp": FUTURE.timestamp(),
                    "sub": "Uowner",
                },
                request=request,
            )

        invalid_client = httpx.AsyncClient(transport=httpx.MockTransport(invalid_issuer))
        invalid_gateway = LabLineGateway(RUNTIME.channel_id, client=invalid_client)
        with patch("textjudgmentlab.authentication.LabLineGateway", return_value=invalid_gateway):
            invalid = self._post(HTTP_AUTHORIZATION="Bearer invalid-issuer")
        async_to_sync(invalid_client.aclose)()

        async def delayed(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.05)
            return httpx.Response(200, json={}, request=request)

        delayed_client = httpx.AsyncClient(transport=httpx.MockTransport(delayed))
        delayed_gateway = LabLineGateway(RUNTIME.channel_id, client=delayed_client)
        with (
            patch("textjudgmentlab.line_gateway._DEADLINE_SECONDS", 0.01),
            patch(
                "textjudgmentlab.authentication.LabLineGateway",
                return_value=delayed_gateway,
            ),
        ):
            timeout = self._post(HTTP_AUTHORIZATION="Bearer delayed")
        async_to_sync(delayed_client.aclose)()

        self.assertEqual(
            (invalid.status_code, invalid.data["error"]["code"]), (401, "reauthentication_required")
        )
        self.assertEqual(
            (timeout.status_code, timeout.data["error"]["code"]), (503, "access_unavailable")
        )

    # テストケース: 実endpointへ過大header/body、malformed JSONを送る。
    # 期待値: 認証・service前に固定上限とparse errorで拒否する。
    def test_enforces_header_body_and_json_limits_on_real_endpoint(self) -> None:
        oversized_authorization = self.client.generic(
            "POST",
            self.url,
            b"{}",
            content_type="application/json",
            HTTP_ORIGIN=RUNTIME.origin,
            HTTP_AUTHORIZATION="Bearer " + "x" * (8 * 1024),
        )
        oversized_body = self.client.generic(
            "POST",
            self.url,
            b'"' + b"x" * (32 * 1024) + b'"',
            content_type="application/json",
            HTTP_ORIGIN=RUNTIME.origin,
        )
        gateway = _Gateway(VerifiedLabIdentity(FUTURE, "a" * 64))
        with patch("textjudgmentlab.authentication.LabLineGateway", return_value=gateway):
            malformed = self.client.generic(
                "POST",
                self.url,
                b"{",
                content_type="application/json",
                HTTP_ORIGIN=RUNTIME.origin,
                HTTP_AUTHORIZATION="Bearer valid",
            )

        self.assertEqual(
            (oversized_authorization.status_code, oversized_authorization.data["error"]["code"]),
            (401, "reauthentication_required"),
        )
        self.assertEqual(
            (oversized_body.status_code, oversized_body.data["error"]["code"]),
            (413, "input_too_large"),
        )
        self.assertEqual(
            (malformed.status_code, malformed.data["error"]["code"]), (400, "invalid_input")
        )

    # テストケース: 実judgment endpointでthrottle結果と想定外例外を発生させる。
    # 期待値: 429/500の固定schemaへ縮約し秘密を返さない。
    def test_normalizes_throttle_and_unexpected_service_failures_on_real_endpoint(self) -> None:
        from textjudgmentlab.types import JudgmentFailure

        class Service:
            async def evaluate(self, principal, request):
                return JudgmentFailure("rate_limited")

        gateway = _Gateway(VerifiedLabIdentity(FUTURE, "a" * 64))
        judgment_url = "/api/labs/text-judgment/judgments"
        payload = {
            "contractVersion": 2,
            "consultationId": "123e4567-e89b-42d3-a456-426614174000",
            "requestId": "123e4567-e89b-42d3-a456-426614174001",
            "revision": 0,
            "text": "通知が届きません",
            "context": {
                "question": "start",
                "confirmed": {"topic": None, "scope": None, "workaround": None, "urgency": None},
                "recentUserTexts": [],
                "impact": "unassessed",
            },
        }
        with (
            patch("textjudgmentlab.authentication.LabLineGateway", return_value=gateway),
            patch(
                "textjudgmentlab.views.build_judgment_service",
                return_value=Service(),
            ),
        ):
            throttled = self.client.post(
                judgment_url,
                payload,
                format="json",
                HTTP_ORIGIN=RUNTIME.origin,
                HTTP_AUTHORIZATION="Bearer valid",
            )
        with (
            patch("textjudgmentlab.authentication.LabLineGateway", return_value=gateway),
            patch(
                "textjudgmentlab.views.build_judgment_service",
                side_effect=RuntimeError("secret-canary"),
            ),
        ):
            unexpected = self.client.post(
                judgment_url,
                payload,
                format="json",
                HTTP_ORIGIN=RUNTIME.origin,
                HTTP_AUTHORIZATION="Bearer valid",
            )

        self.assertEqual(
            (throttled.status_code, throttled.data["error"]["code"]), (429, "rate_limited")
        )
        self.assertEqual(
            (unexpected.status_code, unexpected.data["error"]["code"]), (500, "unexpected")
        )
        self.assertNotIn("secret-canary", unexpected.content.decode())
