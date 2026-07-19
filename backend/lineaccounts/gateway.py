"""LINE Platform との同期 HTTP 通信境界。"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hmac import compare_digest
from typing import Protocol

import httpx

from .runtime import LineAccountRuntime
from .types import ChannelAccessToken, IdToken, LineSubject, UserAccessToken


_API_ORIGIN = "https://api.line.me"
_ID_TOKEN_ISSUER = "https://access.line.me"
_REQUIRED_USER_TOKEN_SCOPES = frozenset(("openid", "profile"))
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_RETRIES = 2


@dataclass(frozen=True, slots=True)
class VerifiedLineIdentity:
    provider_id: str
    subject: LineSubject
    display_name: str


@dataclass(frozen=True, slots=True)
class VerifyIdentitySucceeded:
    identity: VerifiedLineIdentity


@dataclass(frozen=True, slots=True)
class VerifyUserTokenSucceeded:
    subject: LineSubject


@dataclass(frozen=True, slots=True)
class FriendshipSucceeded:
    is_friend: bool


@dataclass(frozen=True, slots=True)
class InvalidLineProof:
    """Credential または本人 binding が無効であることだけを表す。"""


@dataclass(frozen=True, slots=True)
class LinePlatformUnavailable:
    rate_limited: bool = False


@dataclass(frozen=True, slots=True)
class DeauthorizeSucceeded:
    """LINE が空 body の 204 を返したことを表す。"""


@dataclass(frozen=True, slots=True)
class DeauthorizeRejected:
    """外部作用が始まらず LINE が要求を拒否したことを表す。"""


@dataclass(frozen=True, slots=True)
class DeauthorizeUncertain:
    """認可取消の外部作用を断定できないことを表す。"""


VerifyIdentityResult = (
    VerifyIdentitySucceeded | InvalidLineProof | LinePlatformUnavailable
)
VerifyUserTokenResult = (
    VerifyUserTokenSucceeded | InvalidLineProof | LinePlatformUnavailable
)
FriendshipResult = FriendshipSucceeded | InvalidLineProof | LinePlatformUnavailable
DeauthorizeResult = (
    DeauthorizeSucceeded
    | DeauthorizeRejected
    | DeauthorizeUncertain
    | LinePlatformUnavailable
)


class LinePlatformGateway(Protocol):
    def verify_id_token(self, token: IdToken) -> VerifyIdentityResult: ...

    def verify_user_access_token(
        self, token: UserAccessToken, expected_subject: LineSubject
    ) -> VerifyUserTokenResult: ...

    def get_friendship(self, token: UserAccessToken) -> FriendshipResult: ...

    def deauthorize(self, token: UserAccessToken) -> DeauthorizeResult: ...


@dataclass(frozen=True, slots=True)
class _TransportFailure:
    timeout: bool = False


class HttpxLinePlatformGateway:
    """設計で許可された LINE endpoint だけを呼び出す adapter。"""

    def __init__(
        self,
        runtime: LineAccountRuntime,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self._runtime = runtime
        self._client = client or httpx.Client()
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter or (lambda: random.uniform(0.0, 0.025))
        self._timeout = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=5.0)

    def verify_id_token(self, token: IdToken) -> VerifyIdentityResult:
        response = self._read_only_request(
            "POST",
            "/oauth2/v2.1/verify",
            data={
                "id_token": token.reveal_for_remote_call(),
                "client_id": self._runtime.channel_id,
            },
        )
        failure = self._proof_failure(response)
        if failure is not None:
            return failure
        assert isinstance(response, httpx.Response)
        payload = self._json_object(response)
        if payload is None or not self._valid_id_claims(payload):
            return InvalidLineProof()
        return VerifyIdentitySucceeded(
            VerifiedLineIdentity(
                provider_id=self._runtime.provider_id,
                subject=LineSubject(payload["sub"]),
                display_name=payload["name"],
            )
        )

    def verify_user_access_token(
        self, token: UserAccessToken, expected_subject: LineSubject
    ) -> VerifyUserTokenResult:
        verification = self._read_only_request(
            "GET",
            "/oauth2/v2.1/verify",
            params={"access_token": token.reveal_for_remote_call()},
        )
        failure = self._proof_failure(verification)
        if failure is not None:
            return failure
        assert isinstance(verification, httpx.Response)
        verification_payload = self._json_object(verification)
        if verification_payload is None or not self._valid_user_token(
            verification_payload
        ):
            return InvalidLineProof()

        profile = self._read_only_request(
            "GET",
            "/v2/profile",
            headers=self._bearer_headers(token.reveal_for_remote_call()),
        )
        failure = self._proof_failure(profile)
        if failure is not None:
            return failure
        assert isinstance(profile, httpx.Response)
        profile_payload = self._json_object(profile)
        subject = profile_payload.get("userId") if profile_payload else None
        if not self._valid_subject(subject) or not compare_digest(
            subject, expected_subject.reveal_for_identity_binding()
        ):
            return InvalidLineProof()
        return VerifyUserTokenSucceeded(subject=LineSubject(subject))

    def get_friendship(self, token: UserAccessToken) -> FriendshipResult:
        response = self._read_only_request(
            "GET",
            "/friendship/v1/status",
            headers=self._bearer_headers(token.reveal_for_remote_call()),
        )
        failure = self._proof_failure(response)
        if failure is not None:
            return failure
        assert isinstance(response, httpx.Response)
        payload = self._json_object(response)
        friend_flag = payload.get("friendFlag") if payload else None
        if type(friend_flag) is not bool:
            return LinePlatformUnavailable()
        return FriendshipSucceeded(is_friend=friend_flag)

    def deauthorize(self, token: UserAccessToken) -> DeauthorizeResult:
        issued_token = self._issue_stateless_channel_token()
        if not isinstance(issued_token, ChannelAccessToken):
            return issued_token

        try:
            response = self._request(
                "POST",
                "/user/v1/deauthorize",
                headers=self._bearer_headers(issued_token.reveal_for_remote_call()),
                json={"userAccessToken": token.reveal_for_remote_call()},
            )
        except httpx.RequestError:
            return DeauthorizeUncertain()

        if response.status_code == 204 and response.content == b"":
            return DeauthorizeSucceeded()
        if 400 <= response.status_code < 500 and response.status_code != 429:
            return DeauthorizeRejected()
        return DeauthorizeUncertain()

    def _issue_stateless_channel_token(
        self,
    ) -> ChannelAccessToken | DeauthorizeRejected | LinePlatformUnavailable:
        response = self._read_only_request(
            "POST",
            "/oauth2/v3/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._runtime.channel_id,
                "client_secret": self._runtime.channel_secret.get_secret_value(),
            },
        )
        if isinstance(response, _TransportFailure):
            return LinePlatformUnavailable()
        if response.status_code == 429 or response.status_code >= 500:
            return LinePlatformUnavailable(rate_limited=response.status_code == 429)
        if 400 <= response.status_code < 500:
            return DeauthorizeRejected()
        if response.status_code != 200:
            return LinePlatformUnavailable()

        payload = self._json_object(response)
        if payload is None:
            return LinePlatformUnavailable()
        token_type = payload.get("token_type")
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if (
            token_type != "Bearer"
            or not self._valid_credential(access_token)
            or not self._positive_number(expires_in)
        ):
            return LinePlatformUnavailable()
        return ChannelAccessToken(access_token)

    def _read_only_request(
        self, method: str, path: str, **kwargs: object
    ) -> httpx.Response | _TransportFailure:
        for retry_number in range(_MAX_RETRIES + 1):
            try:
                response = self._request(method, path, **kwargs)
            except httpx.TimeoutException:
                if retry_number < _MAX_RETRIES:
                    self._retry_delay(retry_number)
                    continue
                return _TransportFailure(timeout=True)
            except httpx.RequestError:
                return _TransportFailure()

            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and retry_number < _MAX_RETRIES:
                response.close()
                self._retry_delay(retry_number)
                continue
            return response
        raise AssertionError("unreachable")

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        return self._client.request(
            method,
            f"{_API_ORIGIN}{path}",
            timeout=self._timeout,
            follow_redirects=False,
            **kwargs,
        )

    def _retry_delay(self, retry_number: int) -> None:
        jitter = self._jitter()
        bounded_jitter = min(max(jitter, 0.0), 0.05)
        self._sleep(0.05 * (retry_number + 1) + bounded_jitter)

    def _proof_failure(
        self, response: httpx.Response | _TransportFailure
    ) -> InvalidLineProof | LinePlatformUnavailable | None:
        if isinstance(response, _TransportFailure):
            return LinePlatformUnavailable()
        if response.status_code == 429 or response.status_code >= 500:
            return LinePlatformUnavailable(rate_limited=response.status_code == 429)
        if 400 <= response.status_code < 500:
            return InvalidLineProof()
        if response.status_code != 200:
            return LinePlatformUnavailable()
        return None

    def _valid_id_claims(self, payload: Mapping[str, object]) -> bool:
        issuer = payload.get("iss")
        audience = payload.get("aud")
        expires_at = payload.get("exp")
        subject = payload.get("sub")
        name = payload.get("name")
        return (
            isinstance(issuer, str)
            and compare_digest(issuer, _ID_TOKEN_ISSUER)
            and isinstance(audience, str)
            and compare_digest(audience, self._runtime.channel_id)
            and self._positive_number(expires_at)
            and expires_at > self._clock()
            and self._valid_subject(subject)
            and self._valid_display_name(name)
        )

    def _valid_user_token(self, payload: Mapping[str, object]) -> bool:
        client_id = payload.get("client_id")
        expires_in = payload.get("expires_in")
        scope = payload.get("scope")
        return (
            isinstance(client_id, str)
            and compare_digest(client_id, self._runtime.channel_id)
            and self._positive_number(expires_in)
            and isinstance(scope, str)
            and _REQUIRED_USER_TOKEN_SCOPES.issubset(scope.split())
        )

    @staticmethod
    def _positive_number(value: object) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
        )

    @staticmethod
    def _valid_subject(value: object) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= 255
            and value.isascii()
            and value.isprintable()
        )

    @staticmethod
    def _valid_display_name(value: object) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= 256
            and value.isprintable()
            and not value.isspace()
        )

    @staticmethod
    def _valid_credential(value: object) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= 8192
            and value.isascii()
            and value.isprintable()
        )

    @staticmethod
    def _json_object(response: httpx.Response) -> Mapping[str, object] | None:
        if len(response.content) > _MAX_RESPONSE_BYTES:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, Mapping):
            return None
        return payload

    @staticmethod
    def _bearer_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}
