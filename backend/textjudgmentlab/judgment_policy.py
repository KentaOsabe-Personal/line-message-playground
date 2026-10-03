from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TypeVar

from .jev_gateway import JevTransportFailure, JevTransportResult
from .types import (
    AdoptionPolicySnapshot,
    Change,
    ChoiceAdoptionPolicy,
    ChoiceDetail,
    ChoiceId,
    Evidence,
    JudgmentDetails,
    JudgmentEvidence,
    JudgmentFailure,
    JudgmentId,
    JudgmentRequest,
    KnownEvidence,
    NeedsReviewEvidence,
    NormalizationDecision,
    NormalizationReason,
    NormalizationResult,
    NormalizedJudgment,
    NoulAdoptionPolicy,
    NoulDetail,
    PolicyCheck,
    Relevance,
    ResultAnswer,
    Scope,
    ScoreAdoptionPolicy,
    ScoreDetail,
    Topic,
    UnmentionedEvidence,
    Workaround,
)

_CHOICES: dict[ChoiceId, tuple[str, ...]] = {
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
_POLICY = AdoptionPolicySnapshot(
    ChoiceAdoptionPolicy(0.70, 0.70, True),
    ScoreAdoptionPolicy("present", 0.70, 1.5),
    NoulAdoptionPolicy(0.80, 0.20),
)

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
        candidate: _number(value[candidate], minimum=0.0, maximum=1.0) for candidate in candidates
    }
    if not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=0.01 + 1e-12):
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


def _choice_decision(detail: ChoiceDetail) -> NormalizationDecision:
    maximum = max(detail.probabilities.values())
    checks = (
        PolicyCheck(
            "confidence_below_threshold",
            detail.confidence,
            "gte",
            _POLICY.choice.min_confidence,
            detail.confidence >= _POLICY.choice.min_confidence,
        ),
        PolicyCheck(
            "probability_below_threshold",
            maximum,
            "gte",
            _POLICY.choice.min_probability,
            maximum >= _POLICY.choice.min_probability,
        ),
        PolicyCheck(
            "maximum_not_unique",
            sum(value == maximum for value in detail.probabilities.values()),
            "eq",
            1,
            sum(value == maximum for value in detail.probabilities.values()) == 1,
        ),
    )
    reasons = tuple(check.rule for check in checks if not check.passed)
    if reasons:
        return NormalizationDecision("needs_review", reasons, checks)
    if detail.choice == "unclear":
        return NormalizationDecision("needs_review", ("unclear",), checks)
    if detail.choice == "unmentioned":
        return NormalizationDecision("unmentioned", ("unmentioned",), checks)
    return NormalizationDecision("eligible", ("eligible",), checks)


T = TypeVar("T")


def _evidence(
    detail: ChoiceDetail, decision: NormalizationDecision, values: Mapping[str, T]
) -> Evidence[T]:
    if decision.status == "needs_review":
        return NeedsReviewEvidence()
    if decision.status == "unmentioned":
        return UnmentionedEvidence()
    return KnownEvidence(values[detail.choice])


def _categorical(
    detail: ChoiceDetail, decision: NormalizationDecision, values: Mapping[str, T], fallback: T
) -> T:
    if decision.status != "eligible":
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
) -> NormalizationResult:
    if isinstance(transport, JevTransportFailure):
        return JudgmentFailure(transport.code)
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
        if not isinstance(urgency_answer, Mapping) or urgency_answer.get("type") != "noul":
            raise _InvalidResponse
        noul = _number(urgency_answer.get("noul"), minimum=0.0, maximum=1.0)
    except KeyError, _InvalidResponse:
        return JudgmentFailure("judge_unavailable")

    decisions: dict[JudgmentId, NormalizationDecision] = {
        key: _choice_decision(detail) for key, detail in choices.items()
    }
    impact_evidence = choices["impact_evidence"]
    adopted = decisions["impact_evidence"].status == "eligible"
    present = impact_evidence.choice == _POLICY.score.required_impact_evidence
    confident = score.confidence >= _POLICY.score.min_confidence
    high = score.score >= _POLICY.score.high_from
    score_checks = (
        PolicyCheck("impact_evidence_not_adopted", adopted, "eq", True, adopted),
        PolicyCheck(
            "impact_evidence_absent",
            impact_evidence.choice,
            "eq",
            _POLICY.score.required_impact_evidence,
            present,
        ),
        PolicyCheck(
            "confidence_below_threshold",
            score.confidence,
            "gte",
            _POLICY.score.min_confidence,
            confident,
        ),
        PolicyCheck("score_high_boundary", score.score, "gte", _POLICY.score.high_from, high),
    )
    score_reasons: list[NormalizationReason] = []
    if not adopted:
        score_reasons.append("impact_evidence_not_adopted")
    elif not present:
        score_reasons.append("impact_evidence_absent")
    if not confident:
        score_reasons.append("confidence_below_threshold")
    if score_reasons:
        impact = "needs_review"
        decisions["impact"] = NormalizationDecision(
            "needs_review", tuple(score_reasons), score_checks
        )
    else:
        impact = "high" if high else "low"
        decisions["impact"] = NormalizationDecision("eligible", ("eligible",), score_checks)

    urgent = noul >= _POLICY.noul.urgent_from
    not_urgent = noul <= _POLICY.noul.not_urgent_through
    noul_checks = (
        PolicyCheck("noul_urgent_boundary", noul, "gte", _POLICY.noul.urgent_from, urgent),
        PolicyCheck(
            "noul_not_urgent_boundary", noul, "lte", _POLICY.noul.not_urgent_through, not_urgent
        ),
    )
    if urgent or not_urgent:
        urgency: Evidence[bool] = KnownEvidence(urgent)
        decisions["urgency"] = NormalizationDecision("eligible", ("eligible",), noul_checks)
    else:
        urgency = NeedsReviewEvidence()
        decisions["urgency"] = NormalizationDecision(
            "needs_review", ("noul_between_thresholds",), noul_checks
        )

    topic_values: dict[str, Topic | str] = {
        "missing_notification": Topic.MISSING_NOTIFICATION,
        "notification_settings": Topic.NOTIFICATION_SETTINGS,
        "both": "both",
    }
    evidence = JudgmentEvidence(
        topic=_evidence(choices["topic"], decisions["topic"], topic_values),
        relevance=_categorical(
            choices["relevance"],
            decisions["relevance"],
            {
                "in_scope": Relevance.IN_SCOPE,
                "mixed": Relevance.MIXED,
                "out_of_scope": Relevance.OUT_OF_SCOPE,
            },
            Relevance.NEEDS_REVIEW,
        ),
        change=_categorical(
            choices["change"],
            decisions["change"],
            {"keep": Change.KEEP, "restart": Change.RESTART},
            Change.NEEDS_REVIEW,
        ),
        scope=_evidence(
            choices["scope"],
            decisions["scope"],
            {"all": Scope.ALL, "specific": Scope.SPECIFIC, "unknown": Scope.UNKNOWN},
        ),
        workaround=_evidence(
            choices["workaround"],
            decisions["workaround"],
            {
                "can_read": Workaround.CAN_READ,
                "cannot_read": Workaround.CANNOT_READ,
                "unknown": Workaround.UNKNOWN,
            },
        ),
        result=_evidence(
            choices["result"],
            decisions["result"],
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
    return NormalizedJudgment(
        consultation_id=request.consultation_id,
        request_id=request.request_id,
        revision=request.revision,
        model=expected_model,
        evidence=evidence,
        policy=_POLICY,
        normalization=decisions,
        details=JudgmentDetails(
            choices=choices,
            score=score,
            noul=NoulDetail(noul),
            jev_elapsed_ms=transport.elapsed_ms,
        ),
    )
