from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any

import httpx

from .runtime import SecretValue


_JEV_URL = "https://api.typesafe.ai/v1/systemone"
_DEADLINE_SECONDS = 8.0
_MAX_RESPONSE_BYTES = 128 * 1024


@dataclass(frozen=True, slots=True)
class JevTransportSuccess:
    payload: Mapping[str, Any]
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class JevTransportFailure:
    code: str = "judge_unavailable"


JevTransportResult = JevTransportSuccess | JevTransportFailure


class JevGateway:
    def __init__(
        self,
        api_key: SecretValue,
        *,
        client: httpx.AsyncClient | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._monotonic = monotonic_clock
        self._timeout = httpx.Timeout(_DEADLINE_SECONDS, connect=2.0)

    async def evaluate(self, payload: Mapping[str, Any]) -> JevTransportResult:
        started_at = self._monotonic()
        client = self._client or httpx.AsyncClient()
        try:
            result = await self._evaluate(payload, client)
        finally:
            if self._client is None:
                await client.aclose()
        if isinstance(result, JevTransportFailure):
            return result
        elapsed_ms = max(0.0, (self._monotonic() - started_at) * 1000.0)
        return JevTransportSuccess(result, elapsed_ms)

    async def _evaluate(
        self, payload: Mapping[str, Any], client: httpx.AsyncClient
    ) -> Mapping[str, Any] | JevTransportFailure:
        try:
            async with asyncio.timeout(_DEADLINE_SECONDS):
                async with client.stream(
                    "POST",
                    _JEV_URL,
                    headers={
                        "Authorization": (
                            f"Bearer {self._api_key.reveal_for_remote_call()}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json=dict(payload),
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        return JevTransportFailure()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > _MAX_RESPONSE_BYTES:
                            return JevTransportFailure()
        except (TimeoutError, httpx.RequestError, RuntimeError):
            return JevTransportFailure()

        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            return JevTransportFailure()
        if (
            not isinstance(decoded, Mapping)
            or not isinstance(decoded.get("model"), str)
            or not isinstance(decoded.get("answers"), Mapping)
        ):
            return JevTransportFailure()
        return dict(decoded)
