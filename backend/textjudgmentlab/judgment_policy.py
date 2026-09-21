from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, TypeVar

from .jev_gateway import JevTransportFailure, JevTransportResult
from .types import (
    Change,
    ChoiceDetail,
    Evidence,
    JudgmentDetails,
    JudgmentEvidence,
    JudgmentFailure,
    JudgmentRequest,
    JudgmentResult,
    JudgmentSuccess,
    KnownEvidence,
    NeedsReviewEvidence,
    NoulDetail,
    Relevance,
    ResultAnswer,
    Scope,
    ScoreDetail,
    Topic,
    UnmentionedEvidence,
    Workaround,
)


_CHOICES: dict[str, tuple[str, ...]] = {
    "topic": (
        "missing_notification",
        "notification_settings",
        "both",
        "unmentioned",
        "unclear",
    ),
    "relevance": ("in_scope", "mixed", "out_of_scope", "unclear"),
    "change": ("keep", "restart", "unclear"),
    "scope": ("all", "specific", "unknown", "unmentioned", "unclear"),
    "workaround": (
        "can_read",
        "cannot_read",
        "unknown",
        "unmentioned",
        "unclear",
    ),
    "result": (
        "done",
        "not_done",
        "not_tried",
        "cannot_check",
        "unmentioned",
        "unclear",
    ),
    "impact_evidence": ("present", "absent", "unclear"),
}
_ANSWER_IDS = (*_CHOICES, "impact", "urgency")
_PUBLIC_SCORE_LEGEND = {
    "0": "支障なし",
    "1": "不便だが別の操作で目的を達成できる",
    "2": "目的を達成できない",
}


class _InvalidResponse(ValueError):
    pass


def _number(value: object, *, minimum: float, maximum: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise _InvalidResponse
    return float(value)


def _probabilities(value: object, candidates: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(candidates):
        raise _InvalidResponse
    probabilities = {
        candidate: _number(value[candidate], minimum=0.0, maximum=1.0)
        for candidate in candidates
    }
    if not math.isclose(
        sum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=0.01 + 1e-12
    ):
        raise _InvalidResponse
    return probabilities


def _choice(answer: object, candidates: tuple[str, ...]) -> ChoiceDetail:
    if not isinstance(answer, Mapping) or answer.get("type") != "choice":
        raise _InvalidResponse
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in candidates:
        raise _InvalidResponse
    probabilities = _probabilities(answer.get("probabilities"), candidates)
    confidence = _number(answer.get("confidence"), minimum=0.0, maximum=1.0)
    if probabilities[choice] != max(probabilities.values()):
        raise _InvalidResponse
    return ChoiceDetail(
        choice=choice,
        probabilities=probabilities,
        confidence=confidence,
    )


def _is_adoptable(detail: ChoiceDetail) -> bool:
    maximum = max(detail.probabilities.values())
    return (
        detail.confidence >= 0.70
        and maximum >= 0.70
        and sum(value == maximum for value in detail.probabilities.values()) == 1
    )


T = TypeVar("T")


def _evidence(detail: ChoiceDetail, values: Mapping[str, T]) -> Evidence[T]:
    if not _is_adoptable(detail) or detail.choice == "unclear":
        return NeedsReviewEvidence()
    if detail.choice == "unmentioned":
        return UnmentionedEvidence()
    return KnownEvidence(values[detail.choice])


def _categorical(detail: ChoiceDetail, values: Mapping[str, T], fallback: T) -> T:
    if not _is_adoptable(detail) or detail.choice == "unclear":
        return fallback
    return values[detail.choice]


def _score(answer: object) -> ScoreDetail:
    if not isinstance(answer, Mapping) or answer.get("type") != "score":
        raise _InvalidResponse
    legend = answer.get("legend")
    if (
        not isinstance(legend, Mapping)
        or set(legend) != {"0", "1", "2"}
        or any(not isinstance(value, str) for value in legend.values())
    ):
        raise _InvalidResponse
    return ScoreDetail(
        score=_number(answer.get("score"), minimum=0.0, maximum=2.0),
        legend=_PUBLIC_SCORE_LEGEND,
        probabilities=_probabilities(answer.get("probabilities"), ("0", "1", "2")),
        confidence=_number(answer.get("confidence"), minimum=0.0, maximum=1.0),
    )


def normalize_judgment(
    request: JudgmentRequest,
    *,
    expected_model: str,
    transport: JevTransportResult,
) -> JudgmentResult:
    if isinstance(transport, JevTransportFailure):
        return JudgmentFailure("judge_unavailable")
    try:
        payload = transport.payload
        answers = payload.get("answers")
        if (
            payload.get("model") != expected_model
            or not isinstance(answers, Mapping)
            or set(answers) != set(_ANSWER_IDS)
        ):
            raise _InvalidResponse

        choices = {
            question_id: _choice(answers[question_id], candidates)
            for question_id, candidates in _CHOICES.items()
        }
        score = _score(answers["impact"])
        urgency_answer = answers["urgency"]
        if (
            not isinstance(urgency_answer, Mapping)
            or urgency_answer.get("type") != "noul"
        ):
            raise _InvalidResponse
        noul = _number(urgency_answer.get("noul"), minimum=0.0, maximum=1.0)
    except (KeyError, _InvalidResponse):
        return JudgmentFailure("judge_unavailable")

    impact_evidence = choices["impact_evidence"]
    if (
        _is_adoptable(impact_evidence)
        and impact_evidence.choice == "present"
        and score.confidence >= 0.70
    ):
        impact = "high" if score.score >= 1.5 else "low"
    else:
        impact = "needs_review"

    if noul >= 0.80:
        urgency: Evidence[bool] = KnownEvidence(True)
    elif noul <= 0.20:
        urgency = KnownEvidence(False)
    else:
        urgency = NeedsReviewEvidence()

    topic_values: dict[str, Topic | str] = {
        "missing_notification": Topic.MISSING_NOTIFICATION,
        "notification_settings": Topic.NOTIFICATION_SETTINGS,
        "both": "both",
    }
    evidence = JudgmentEvidence(
        topic=_evidence(choices["topic"], topic_values),
        relevance=_categorical(
            choices["relevance"],
            {
                "in_scope": Relevance.IN_SCOPE,
                "mixed": Relevance.MIXED,
                "out_of_scope": Relevance.OUT_OF_SCOPE,
            },
            Relevance.NEEDS_REVIEW,
        ),
        change=_categorical(
            choices["change"],
            {"keep": Change.KEEP, "restart": Change.RESTART},
            Change.NEEDS_REVIEW,
        ),
        scope=_evidence(
            choices["scope"],
            {"all": Scope.ALL, "specific": Scope.SPECIFIC, "unknown": Scope.UNKNOWN},
        ),
        workaround=_evidence(
            choices["workaround"],
            {
                "can_read": Workaround.CAN_READ,
                "cannot_read": Workaround.CANNOT_READ,
                "unknown": Workaround.UNKNOWN,
            },
        ),
        result=_evidence(
            choices["result"],
            {
                "done": ResultAnswer.DONE,
                "not_done": ResultAnswer.NOT_DONE,
                "not_tried": ResultAnswer.NOT_TRIED,
                "cannot_check": ResultAnswer.CANNOT_CHECK,
            },
        ),
        impact=impact,
        urgency=urgency,
    )
    return JudgmentSuccess(
        consultation_id=request.consultation_id,
        request_id=request.request_id,
        revision=request.revision,
        model=expected_model,
        evidence=evidence,
        details=JudgmentDetails(
            choices=choices,
            score=score,
            noul=NoulDetail(noul),
            jev_elapsed_ms=transport.elapsed_ms,
        ),
    )
