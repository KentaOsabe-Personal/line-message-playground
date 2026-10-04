from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Protocol

from .jev_gateway import JevTransportFailure, JevTransportResult
from .judgment_questions import INTENT_CRITERIA, build_jev_request
from .limits import LabLimitRejected, LabLimits
from .types import JudgmentFailure, JudgmentRequest, JudgmentSuccess, LabPrincipal


class JudgmentGateway(Protocol):
    async def evaluate(self, payload: dict[str, object]) -> JevTransportResult: ...


def _number(value, maximum=1.0) -> float:
    if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("invalid judgment number")
    return value


def _probabilities(value, keys) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ValueError("invalid judgment probabilities")
    result = {key: _number(value[key]) for key in keys}
    if abs(sum(result.values()) - 1) > 0.01:
        raise ValueError("invalid judgment distribution")
    return result


def validated_answers(payload, model) -> dict[str, object]:
    """形と数値範囲だけを検証する。閾値による採用・補正は行わない。"""
    if payload.get("model") != model:
        raise ValueError("unexpected model")
    answers = payload["answers"]
    if not isinstance(answers, Mapping) or set(answers) != {"intent", "sentiment", "urgency"}:
        raise ValueError("invalid answers")
    intent, sentiment, urgency = (answers[key] for key in ("intent", "sentiment", "urgency"))
    if not all(isinstance(item, Mapping) for item in (intent, sentiment, urgency)):
        raise ValueError("invalid answer")
    if (intent.get("type"), sentiment.get("type"), urgency.get("type")) != (
        "choice",
        "score",
        "noul",
    ):
        raise ValueError("invalid types")
    probabilities = _probabilities(intent.get("probabilities"), INTENT_CRITERIA)
    choice = intent.get("choice")
    if (
        not isinstance(choice, str)
        or choice not in probabilities
        or probabilities[choice] != max(probabilities.values())
    ):
        raise ValueError("invalid choice")
    legend = sentiment.get("legend")
    if (
        not isinstance(legend, Mapping)
        or set(legend) != {"0", "1", "2"}
        or not all(isinstance(v, str) and 0 < len(v) <= 200 for v in legend.values())
    ):
        raise ValueError("invalid legend")
    return {
        "intent": {
            "type": "choice",
            "choice": choice,
            "probabilities": probabilities,
            "confidence": _number(intent.get("confidence")),
        },
        "sentiment": {
            "type": "score",
            "score": _number(sentiment.get("score"), 2),
            "legend": dict(legend),
            "probabilities": _probabilities(sentiment.get("probabilities"), ("0", "1", "2")),
            "confidence": _number(sentiment.get("confidence")),
        },
        "urgency": {"type": "noul", "noul": _number(urgency.get("noul"))},
    }


class JudgmentService:
    def __init__(
        self,
        *,
        model: str,
        gateway: JudgmentGateway,
        limits: LabLimits,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._model, self._gateway, self._limits, self._clock = model, gateway, limits, clock

    async def evaluate(
        self, principal: LabPrincipal, request: JudgmentRequest
    ) -> JudgmentSuccess | JudgmentFailure:
        if not principal.is_valid_at(self._clock()):
            return JudgmentFailure("access_expired")
        permit = self._limits.acquire(principal.owner_digest)
        if isinstance(permit, LabLimitRejected):
            return JudgmentFailure("rate_limited")
        with permit:
            try:
                transport = await self._gateway.evaluate(
                    build_jev_request(request.text, model=self._model)
                )
                if not principal.is_valid_at(self._clock()):
                    return JudgmentFailure("access_expired")
                if isinstance(transport, JevTransportFailure):
                    return JudgmentFailure(transport.code)
                answers = validated_answers(transport.payload, self._model)
                elapsed = _number(transport.elapsed_ms, float("inf"))
                return JudgmentSuccess(self._model, answers, elapsed)
            except ValueError, KeyError, TypeError, AttributeError:
                return JudgmentFailure("judge_unavailable")
            except Exception:
                return JudgmentFailure("unexpected")
