from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from .jev_gateway import JevTransportResult
from .judgment_policy import normalize_judgment
from .judgment_questions import build_jev_request
from .limits import LabLimitRejected, LabLimits
from .types import JudgmentFailure, JudgmentRequest, JudgmentResult, LabPrincipal


class JudgmentGateway(Protocol):
    async def evaluate(self, payload: dict[str, object]) -> JevTransportResult: ...


class JudgmentService:
    def __init__(
        self,
        *,
        model: str,
        gateway: JudgmentGateway,
        limits: LabLimits,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._model = model
        self._gateway = gateway
        self._limits = limits
        self._clock = clock

    async def evaluate(
        self, principal: LabPrincipal, request: JudgmentRequest
    ) -> JudgmentResult:
        if not principal.is_valid_at(self._clock()):
            return JudgmentFailure("access_expired")

        permit = self._limits.acquire(principal.owner_digest)
        if isinstance(permit, LabLimitRejected):
            return JudgmentFailure("rate_limited")

        with permit:
            payload = build_jev_request(request, model=self._model)
            try:
                transport = await self._gateway.evaluate(payload)
            except Exception:
                return JudgmentFailure("unexpected")
            if not principal.is_valid_at(self._clock()):
                return JudgmentFailure("access_expired")
            return normalize_judgment(
                request,
                expected_model=self._model,
                transport=transport,
            )
