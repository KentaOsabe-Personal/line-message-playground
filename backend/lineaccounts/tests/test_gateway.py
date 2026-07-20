from __future__ import annotations

import json
from collections.abc import Callable
from unittest import TestCase
from uuid import UUID

import httpx

from lineaccounts.gateway import (
    DeauthorizeRejected,
    DeauthorizeSucceeded,
    DeauthorizeUncertain,
    FriendshipSucceeded,
    HttpxLinePlatformGateway,
    InvalidLineProof,
    LinePlatformUnavailable,
    VerifyIdentitySucceeded,
    VerifyUserTokenSucceeded,
)
from lineaccounts.runtime import (
    LineAccountRuntime,
    OwnerEligibilityUnavailable,
    SecretValue,
)
from lineaccounts.types import IdToken, LineSubject, UserAccessToken


def runtime() -> LineAccountRuntime:
    return LineAccountRuntime(
        channel_id="1234567890",
        channel_secret=SecretValue("channel-secret-canary"),
        provider_id="0012345678",
        linked_channel_public_id=UUID("12345678-1234-5678-9234-567812345678"),
        owner_eligibility=OwnerEligibilityUnavailable(),
    )


def gateway_for(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleeps: list[float] | None = None,
) -> HttpxLinePlatformGateway:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpxLinePlatformGateway(
        runtime(),
        client=client,
        clock=lambda: 1_000.0,
        sleep=(sleeps.append if sleeps is not None else lambda _delay: None),
        jitter=lambda: 0.0,
    )


def valid_id_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "iss": "https://access.line.me",
        "aud": "1234567890",
        "exp": 1_001,
        "sub": "U1234567890abcdef",
        "name": "Owner",
    }
    payload.update(changes)
    return payload


class ReadOnlyTransportPolicyTests(TestCase):
    # テストケース: 429と5xxが続いた後に正常なID token検証応答を受け取る
    # 期待値: 共通timeoutを適用し、短い待機を挟んだ最大2回の再送で成功する
    def test_retries_only_429_and_5xx_twice_with_bounded_timeouts(self) -> None:
        requests: list[httpx.Request] = []
        sleeps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(429)
            if len(requests) == 2:
                return httpx.Response(503)
            return httpx.Response(200, json=valid_id_payload())

        result = gateway_for(handler, sleeps=sleeps).verify_id_token(
            IdToken("id-token-canary")
        )

        self.assertIsInstance(result, VerifyIdentitySucceeded)
        self.assertEqual(len(requests), 3)
        self.assertEqual(sleeps, [0.05, 0.1])
        for request in requests:
            self.assertEqual(
                request.extensions["timeout"],
                {"connect": 2.0, "read": 5.0, "write": 5.0, "pool": 5.0},
            )

    # テストケース: 400または外部originへのredirect応答を受け取る
    # 期待値: 400を再送せず、redirect先にも追従しない
    def test_does_not_retry_400_or_follow_redirects(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/oauth2/v2.1/verify":
                return httpx.Response(400, json={"error": "id-token-canary"})
            raise AssertionError("redirect was followed")

        result = gateway_for(handler).verify_id_token(IdToken("id-token-canary"))

        self.assertIsInstance(result, InvalidLineProof)
        self.assertEqual(len(requests), 1)

        requests.clear()

        def redirect_handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(
                    302,
                    headers={"Location": "https://attacker.example/token"},
                )
            raise AssertionError("redirect was followed")

        redirect_result = gateway_for(redirect_handler).verify_id_token(
            IdToken("id-token-canary")
        )
        self.assertIsInstance(redirect_result, LinePlatformUnavailable)
        self.assertEqual(len(requests), 1)

    # テストケース: read-only LINE通信が毎回read timeoutになる
    # 期待値: 最大2回だけ再送し、安全な依存障害へ変換する
    def test_retries_timeout_at_most_twice(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("token-canary", request=request)

        result = gateway_for(handler).get_friendship(
            UserAccessToken("user-token-canary")
        )

        self.assertIsInstance(result, LinePlatformUnavailable)
        self.assertEqual(attempts, 3)
        self.assertNotIn("token-canary", repr(result))


class IdTokenVerificationTests(TestCase):
    # テストケース: LINEへraw ID tokenを送り、正しい本人証明応答を受け取る
    # 期待値: runtimeのproviderと検証済みsubject・表示名だけを返す
    def test_posts_raw_token_and_returns_verified_provider_identity(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/oauth2/v2.1/verify")
            self.assertEqual(
                request.content.decode(),
                "id_token=id-token-canary&client_id=1234567890",
            )
            return httpx.Response(200, json=valid_id_payload())

        result = gateway_for(handler).verify_id_token(IdToken("id-token-canary"))

        self.assertIsInstance(result, VerifyIdentitySucceeded)
        assert isinstance(result, VerifyIdentitySucceeded)
        self.assertEqual(result.identity.provider_id, "0012345678")
        self.assertEqual(result.identity.display_name, "Owner")
        self.assertEqual(
            result.identity.subject.reveal_for_identity_binding(),
            "U1234567890abcdef",
        )
        self.assertNotIn("U1234567890abcdef", repr(result))

    # テストケース: issuer・audience・expiry・subject・nameのいずれかが不正である
    # 期待値: 本人証明を拒否し、応答payloadを結果へ露出しない
    def test_rejects_invalid_claims_without_exposing_payload(self) -> None:
        invalid_payloads = (
            valid_id_payload(iss="https://attacker.example"),
            valid_id_payload(aud="9999999999"),
            valid_id_payload(exp=1_000),
            valid_id_payload(exp=True),
            valid_id_payload(sub=""),
            valid_id_payload(name=""),
            valid_id_payload(name=None),
        )

        for payload in invalid_payloads:
            with self.subTest(payload=tuple(payload)):
                result = gateway_for(
                    lambda _request, value=payload: httpx.Response(200, json=value)
                ).verify_id_token(IdToken("id-token-canary"))
                self.assertIsInstance(result, InvalidLineProof)
                self.assertNotIn(str(payload), repr(result))

    # テストケース: ID token検証APIが200で非JSON bodyを返す
    # 期待値: bodyを認証根拠にせず、安全な本人証明拒否へ変換する
    def test_rejects_non_json_success_response(self) -> None:
        result = gateway_for(
            lambda _request: httpx.Response(200, content=b"id-token-canary")
        ).verify_id_token(IdToken("id-token-canary"))

        self.assertIsInstance(result, InvalidLineProof)
        self.assertNotIn("id-token-canary", repr(result))


class UserAccessTokenVerificationTests(TestCase):
    # テストケース: user tokenのchannel・期限・scopeとprofile subjectが正しい
    # 期待値: verifyとprofileの両方を確認した本人bindingだけを返す
    def test_verifies_channel_expiry_scopes_and_profile_subject(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/oauth2/v2.1/verify":
                self.assertEqual(request.url.params["access_token"], "user-token-canary")
                return httpx.Response(
                    200,
                    json={
                        "client_id": "1234567890",
                        "expires_in": 60,
                        "scope": "openid profile",
                    },
                )
            self.assertEqual(request.url.path, "/v2/profile")
            self.assertEqual(request.headers["Authorization"], "Bearer user-token-canary")
            return httpx.Response(
                200,
                json={"userId": "U1234567890abcdef", "displayName": "Owner"},
            )

        result = gateway_for(handler).verify_user_access_token(
            UserAccessToken("user-token-canary"),
            LineSubject("U1234567890abcdef"),
        )

        self.assertIsInstance(result, VerifyUserTokenSucceeded)
        self.assertEqual(len(requests), 2)
        self.assertNotIn("user-token-canary", repr(result))
        self.assertNotIn("U1234567890abcdef", repr(result))

    # テストケース: user tokenのchannel・期限・必須scopeのいずれかが不正である
    # 期待値: profileを呼び出す前に本人証明を拒否する
    def test_rejects_wrong_channel_expiry_or_missing_scope_before_profile(self) -> None:
        invalid_verifications = (
            {"client_id": "different", "expires_in": 60, "scope": "openid profile"},
            {"client_id": "1234567890", "expires_in": 0, "scope": "openid profile"},
            {"client_id": "1234567890", "expires_in": True, "scope": "openid profile"},
            {"client_id": "1234567890", "expires_in": 60, "scope": "profile"},
            {"client_id": "1234567890", "expires_in": 60, "scope": "openid"},
        )
        for verification in invalid_verifications:
            requests = 0

            def handler(
                _request: httpx.Request, value: dict[str, object] = verification
            ) -> httpx.Response:
                nonlocal requests
                requests += 1
                return httpx.Response(200, json=value)

            result = gateway_for(handler).verify_user_access_token(
                UserAccessToken("user-token-canary"),
                LineSubject("U1234567890abcdef"),
            )
            self.assertIsInstance(result, InvalidLineProof)
            self.assertEqual(requests, 1)

    # テストケース: profile subjectが保存済みidentity subjectと一致しない
    # 期待値: constant-time比較境界で拒否し、どちらのsubjectも結果へ露出しない
    def test_rejects_profile_subject_mismatch_in_constant_time_boundary(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/v2.1/verify":
                return httpx.Response(
                    200,
                    json={
                        "client_id": "1234567890",
                        "expires_in": 60,
                        "scope": "openid profile",
                    },
                )
            return httpx.Response(
                200,
                json={"userId": "Uattacker", "displayName": "Attacker"},
            )

        result = gateway_for(handler).verify_user_access_token(
            UserAccessToken("user-token-canary"),
            LineSubject("U1234567890abcdef"),
        )

        self.assertIsInstance(result, InvalidLineProof)
        self.assertNotIn("Uattacker", repr(result))


class FriendshipTests(TestCase):
    # テストケース: friendship APIがbooleanのfriendFlagを返す
    # 期待値: booleanだけをtyped friendship結果へ変換する
    def test_returns_only_boolean_friendship(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/friendship/v1/status")
            self.assertEqual(request.headers["Authorization"], "Bearer user-token-canary")
            return httpx.Response(200, json={"friendFlag": False})

        result = gateway_for(handler).get_friendship(
            UserAccessToken("user-token-canary")
        )

        self.assertEqual(result, FriendshipSucceeded(is_friend=False))

    # テストケース: friendship APIがboolean以外のfriendFlagを返す
    # 期待値: truthy変換せず、安全な依存障害として扱う
    def test_rejects_non_boolean_friendship(self) -> None:
        for value in (0, 1, "true", None):
            with self.subTest(value=value):
                result = gateway_for(
                    lambda _request, flag=value: httpx.Response(
                        200, json={"friendFlag": flag}
                    )
                ).get_friendship(UserAccessToken("user-token-canary"))
                self.assertIsInstance(result, LinePlatformUnavailable)


class DeauthorizeTests(TestCase):
    # テストケース: stateless channel token発行後に空bodyの204を受け取る
    # 期待値: tokenとsecretを保持せず、認可取消成功として返す
    def test_issues_stateless_token_then_accepts_only_empty_204(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/oauth2/v3/token":
                self.assertEqual(request.method, "POST")
                self.assertEqual(
                    request.content.decode(),
                    "grant_type=client_credentials&client_id=1234567890&client_secret=channel-secret-canary",
                )
                return httpx.Response(
                    200,
                    json={
                        "token_type": "Bearer",
                        "access_token": "channel-token-canary",
                        "expires_in": 900,
                    },
                )
            self.assertEqual(request.url.path, "/user/v1/deauthorize")
            self.assertEqual(request.headers["Authorization"], "Bearer channel-token-canary")
            self.assertEqual(
                json.loads(request.content),
                {"userAccessToken": "user-token-canary"},
            )
            return httpx.Response(204)

        result = gateway_for(handler).deauthorize(
            UserAccessToken("user-token-canary")
        )

        self.assertIsInstance(result, DeauthorizeSucceeded)
        self.assertEqual(len(requests), 2)
        self.assertNotIn("channel-token-canary", repr(result))

    # テストケース: deauthorize本体が5xxを返す
    # 期待値: 同一request内で再送せず、外部結果不確定として返す
    def test_does_not_retry_deauthorize_when_result_is_uncertain(self) -> None:
        deauthorize_attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal deauthorize_attempts
            if request.url.path == "/oauth2/v3/token":
                return httpx.Response(
                    200,
                    json={
                        "token_type": "Bearer",
                        "access_token": "channel-token-canary",
                        "expires_in": 900,
                    },
                )
            deauthorize_attempts += 1
            return httpx.Response(503, json={"message": "user-token-canary"})

        result = gateway_for(handler).deauthorize(
            UserAccessToken("user-token-canary")
        )

        self.assertIsInstance(result, DeauthorizeUncertain)
        self.assertEqual(deauthorize_attempts, 1)
        self.assertNotIn("user-token-canary", repr(result))

    # テストケース: deauthorize本体が429 rate limitを返す。
    # 期待値: 自動再送せず外部結果不確定へ分類し、tokenを結果へ露出しない。
    def test_classifies_deauthorize_429_as_uncertain_without_retry(self) -> None:
        deauthorize_attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal deauthorize_attempts
            if request.url.path == "/oauth2/v3/token":
                return httpx.Response(
                    200,
                    json={
                        "token_type": "Bearer",
                        "access_token": "channel-token-canary",
                        "expires_in": 900,
                    },
                )
            deauthorize_attempts += 1
            return httpx.Response(429, json={"message": "user-token-canary"})

        result = gateway_for(handler).deauthorize(
            UserAccessToken("user-token-canary")
        )

        self.assertIsInstance(result, DeauthorizeUncertain)
        self.assertEqual(deauthorize_attempts, 1)
        self.assertNotIn("user-token-canary", repr(result))

    # テストケース: deauthorize本体が400を返す
    # 期待値: 成功扱いせず、認可取消拒否として返す
    def test_classifies_deauthorize_400_as_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/v3/token":
                return httpx.Response(
                    200,
                    json={
                        "token_type": "Bearer",
                        "access_token": "channel-token-canary",
                        "expires_in": 900,
                    },
                )
            return httpx.Response(400)

        result = gateway_for(handler).deauthorize(
            UserAccessToken("user-token-canary")
        )

        self.assertIsInstance(result, DeauthorizeRejected)

    # テストケース: deauthorize本体が非空204・切断・timeoutのいずれかになる
    # 期待値: 自動再送せず、すべて外部結果不確定へ収束させる
    def test_treats_nonempty_204_and_disconnect_as_uncertain_without_retry(self) -> None:
        for terminal in ("body", "disconnect", "timeout"):
            with self.subTest(terminal=terminal):
                deauthorize_attempts = 0

                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal deauthorize_attempts
                    if request.url.path == "/oauth2/v3/token":
                        return httpx.Response(
                            200,
                            json={
                                "token_type": "Bearer",
                                "access_token": "channel-token-canary",
                                "expires_in": 900,
                            },
                        )
                    deauthorize_attempts += 1
                    if terminal == "body":
                        return httpx.Response(204, content=b"unexpected")
                    if terminal == "timeout":
                        raise httpx.ReadTimeout(
                            "user-token-canary", request=request
                        )
                    raise httpx.RemoteProtocolError(
                        "channel-token-canary", request=request
                    )

                result = gateway_for(handler).deauthorize(
                    UserAccessToken("user-token-canary")
                )
                self.assertIsInstance(result, DeauthorizeUncertain)
                self.assertEqual(deauthorize_attempts, 1)
                self.assertNotIn("channel-token-canary", repr(result))

    # テストケース: stateless token発行が2回失敗後、不正な成功payloadを返す
    # 期待値: token発行だけを限定再送し、不正payloadを依存障害へ変換する
    def test_retries_stateless_token_issuance_but_validates_response(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    "token_type": "not-bearer",
                    "access_token": "channel-token-canary",
                    "expires_in": 900,
                },
            )

        result = gateway_for(handler).deauthorize(
            UserAccessToken("user-token-canary")
        )

        self.assertIsInstance(result, LinePlatformUnavailable)
        self.assertEqual(attempts, 3)
        self.assertNotIn("channel-token-canary", repr(result))
