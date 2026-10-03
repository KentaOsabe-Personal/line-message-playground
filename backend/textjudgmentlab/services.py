from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol

from .jev_gateway import JevTransportResult
from .judgment_policy import normalize_judgment
from .judgment_questions import build_judgment_input
from .limits import LabLimitRejected, LabLimits
from .types import (
    JudgmentFailure,
    JudgmentInspection,
    JudgmentRequest,
    JudgmentResult,
    JudgmentSuccess,
    LabPrincipal,
)


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

    async def evaluate(self, principal: LabPrincipal, request: JudgmentRequest) -> JudgmentResult:
        if not principal.is_valid_at(self._clock()):
            return JudgmentFailure("access_expired")

        permit = self._limits.acquire(principal.owner_digest)
        if isinstance(permit, LabLimitRejected):
            return JudgmentFailure("rate_limited")

        with permit:
            built = build_judgment_input(request, model=self._model)
            payload = built.to_payload()
            started_at = monotonic()
            try:
                transport = await self._gateway.evaluate(payload)
                normalized = normalize_judgment(
                    request,
                    expected_model=self._model,
                    transport=transport,
                )
                elapsed_ms = max(0.0, (monotonic() - started_at) * 1000.0)
            except Exception:
                return JudgmentFailure("unexpected")
            if not principal.is_valid_at(self._clock()):
                return JudgmentFailure("access_expired")
            if isinstance(normalized, JudgmentFailure):
                return normalized
            return JudgmentSuccess(
                consultation_id=request.consultation_id,
                request_id=request.request_id,
                revision=request.revision,
                model=built.model,
                evidence=normalized.evidence,
                details=replace(normalized.details, jev_elapsed_ms=elapsed_ms),
                inspection=JudgmentInspection(
                    state=built.state,
                    questions=built.questions,
                    question_version=built.question_version,
                    policy=normalized.policy,
                    normalization=normalized.normalization,
                ),
            )
