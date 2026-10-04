from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest

import httpx

_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"
_ISSUER = "https://access.line.me"
_DEADLINE_SECONDS = 4.0
_MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedLabIdentity:
    expires_at: datetime
    subject_digest: str = field(repr=False)

    def __repr__(self) -> str:
        return f"VerifiedLabIdentity(expires_at={self.expires_at!r}, subject_digest=<redacted>)"


@dataclass(frozen=True, slots=True)
class LineIdentityRejected:
    code: str

    def __post_init__(self) -> None:
        if self.code not in {"wrong_channel", "invalid_proof"}:
            raise ValueError("invalid LINE identity rejection")


@dataclass(frozen=True, slots=True)
class LineIdentityUnavailable:
    pass


LabLineResult = VerifiedLabIdentity | LineIdentityRejected | LineIdentityUnavailable


class LabLineGateway:
    def __init__(
        self,
        channel_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._channel_id = channel_id
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._clock = clock
        self._timeout = httpx.Timeout(_DEADLINE_SECONDS)

    async def verify(self, id_token: str) -> LabLineResult:
        try:
            return await self._verify(id_token)
        finally:
            if self._owns_client:
                await self._client.aclose()

    async def _verify(self, id_token: str) -> LabLineResult:
        try:
            async with asyncio.timeout(_DEADLINE_SECONDS):
                response = await self._client.post(
                    _VERIFY_URL,
                    data={"id_token": id_token, "client_id": self._channel_id},
                    timeout=self._timeout,
                    follow_redirects=False,
                )
        except TimeoutError, httpx.RequestError:
            return LineIdentityUnavailable()

        if response.status_code == 400:
            payload = self._payload(response)
            if payload is not None and self._is_wrong_channel_error(payload):
                return LineIdentityRejected("wrong_channel")
            return LineIdentityRejected("invalid_proof")
        if response.status_code != 200:
            return LineIdentityUnavailable()

        payload = self._payload(response)
        if payload is None:
            return LineIdentityUnavailable()
        audience = payload.get("aud")
        if isinstance(audience, str) and not compare_digest(audience, self._channel_id):
            return LineIdentityRejected("wrong_channel")
        if not self._valid_claims(payload):
            return LineIdentityRejected("invalid_proof")

        subject = payload["sub"]
        expires_at = datetime.fromtimestamp(float(payload["exp"]), UTC)
        digest = sha256(f"{self._channel_id}:{subject}".encode()).hexdigest()
        return VerifiedLabIdentity(expires_at=expires_at, subject_digest=digest)

    def _valid_claims(self, payload: Mapping[str, object]) -> bool:
        issuer = payload.get("iss")
        audience = payload.get("aud")
        expires_at = payload.get("exp")
        subject = payload.get("sub")
        return (
            isinstance(issuer, str)
            and compare_digest(issuer, _ISSUER)
            and isinstance(audience, str)
            and compare_digest(audience, self._channel_id)
            and isinstance(expires_at, (int, float))
            and not isinstance(expires_at, bool)
            and math.isfinite(expires_at)
            and expires_at > self._clock().timestamp()
            and isinstance(subject, str)
            and 0 < len(subject) <= 255
            and subject.isascii()
            and subject.isprintable()
        )

    @staticmethod
    def _payload(response: httpx.Response) -> Mapping[str, object] | None:
        if len(response.content) > _MAX_RESPONSE_BYTES:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, Mapping) else None

    @staticmethod
    def _is_wrong_channel_error(payload: Mapping[str, object]) -> bool:
        error = payload.get("error")
        description = payload.get("error_description")
        normalized = description.lower() if isinstance(description, str) else ""
        return error == "invalid_request" and (
            "client_id" in normalized
            or "client id" in normalized
            or "invalid idtoken audience" in normalized
        )
