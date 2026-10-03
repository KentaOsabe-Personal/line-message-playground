from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Generic, Literal, Mapping, TypeVar
from uuid import UUID


class Topic(StrEnum):
    MISSING_NOTIFICATION = "missing_notification"
    NOTIFICATION_SETTINGS = "notification_settings"


class Scope(StrEnum):
    ALL = "all"
    SPECIFIC = "specific"
    UNKNOWN = "unknown"


class Workaround(StrEnum):
    CAN_READ = "can_read"
    CANNOT_READ = "cannot_read"
    UNKNOWN = "unknown"


class QuestionId(StrEnum):
    START = "start"
    TOPIC = "topic"
    SCOPE = "scope"
    WORKAROUND = "workaround"
    URGENCY = "urgency"
    RESULT = "result"


class ResultAnswer(StrEnum):
    DONE = "done"
    NOT_DONE = "not_done"
    NOT_TRIED = "not_tried"
    CANNOT_CHECK = "cannot_check"


class Impact(StrEnum):
    UNASSESSED = "unassessed"
    NEEDS_REVIEW = "needs_review"
    LOW = "low"
    HIGH = "high"


class Relevance(StrEnum):
    IN_SCOPE = "in_scope"
    MIXED = "mixed"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_REVIEW = "needs_review"


class Change(StrEnum):
    KEEP = "keep"
    RESTART = "restart"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True, repr=False)
class LabPrincipal:
    expires_at: datetime
    owner_digest: str = field(repr=False)

    def is_valid_at(self, now: datetime) -> bool:
        return now < self.expires_at

    @property
    def is_authenticated(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"LabPrincipal(expires_at={self.expires_at!r}, owner_digest=<redacted>)"


@dataclass(frozen=True, slots=True)
class ConfirmedAnswers:
    topic: Topic | None
    scope: Scope | None
    workaround: Workaround | None
    urgency: bool | None


@dataclass(frozen=True, slots=True)
class JudgmentContext:
    question: QuestionId
    confirmed: ConfirmedAnswers
    recent_user_texts: tuple[str, ...]
    impact: Impact


@dataclass(frozen=True, slots=True)
class JudgmentRequest:
    contract_version: Literal[2]
    consultation_id: UUID
    request_id: UUID
    revision: int
    text: str
    context: JudgmentContext


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class KnownEvidence(Generic[T]):
    value: T
    kind: Literal["known"] = "known"


@dataclass(frozen=True, slots=True)
class UnmentionedEvidence:
    kind: Literal["unmentioned"] = "unmentioned"


@dataclass(frozen=True, slots=True)
class NeedsReviewEvidence:
    kind: Literal["needs_review"] = "needs_review"


Evidence = KnownEvidence[T] | UnmentionedEvidence | NeedsReviewEvidence


@dataclass(frozen=True, slots=True)
class ChoiceDetail:
    choice: str
    probabilities: Mapping[str, float]
    confidence: float
    type: Literal["choice"] = "choice"

    def __post_init__(self) -> None:
        object.__setattr__(self, "probabilities", MappingProxyType(dict(self.probabilities)))


@dataclass(frozen=True, slots=True)
class ScoreDetail:
    score: float
    legend: Mapping[str, str]
    probabilities: Mapping[str, float]
    confidence: float
    type: Literal["score"] = "score"

    def __post_init__(self) -> None:
        object.__setattr__(self, "legend", MappingProxyType(dict(self.legend)))
        object.__setattr__(self, "probabilities", MappingProxyType(dict(self.probabilities)))


@dataclass(frozen=True, slots=True)
class NoulDetail:
    noul: float
    type: Literal["noul"] = "noul"


@dataclass(frozen=True, slots=True)
class JudgmentEvidence:
    topic: Evidence[Topic | Literal["both"]]
    relevance: Relevance
    change: Change
    scope: Evidence[Scope]
    workaround: Evidence[Workaround]
    result: Evidence[ResultAnswer]
    impact: Literal["low", "high", "needs_review"]
    urgency: Evidence[bool]


@dataclass(frozen=True, slots=True)
class JudgmentDetails:
    choices: Mapping[str, ChoiceDetail]
    score: ScoreDetail
    noul: NoulDetail
    jev_elapsed_ms: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "choices", MappingProxyType(dict(self.choices)))


@dataclass(frozen=True, slots=True)
class NormalizedJudgment:
    """検証済みの判定と、同じ処理の分岐で生成した採用可否の記録。HTTP応答はサービスで組み立てる。"""

    consultation_id: UUID
    request_id: UUID
    revision: int
    model: str
    evidence: JudgmentEvidence
    details: JudgmentDetails
    policy: "AdoptionPolicySnapshot"
    normalization: Mapping["JudgmentId", "NormalizationDecision"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalization", MappingProxyType(dict(self.normalization)))


@dataclass(frozen=True, slots=True)
class JudgmentFailure:
    code: Literal[
        "invalid_request",
        "rate_limited",
        "access_expired",
        "judge_unavailable",
        "judge_timeout",
        "configuration_unavailable",
        "unexpected",
    ]


NormalizationResult = NormalizedJudgment | JudgmentFailure


ChoiceId = Literal[
    "topic", "relevance", "change", "scope", "workaround", "result", "impact_evidence"
]
JudgmentId = ChoiceId | Literal["impact", "urgency"]
NormalizationReason = Literal[
    "eligible",
    "unmentioned",
    "unclear",
    "confidence_below_threshold",
    "probability_below_threshold",
    "maximum_not_unique",
    "impact_evidence_not_adopted",
    "impact_evidence_absent",
    "noul_between_thresholds",
]


@dataclass(frozen=True, slots=True)
class SentChoiceQuestion:
    instructions: str
    criteria: Mapping[str, str]
    type: Literal["choice"] = "choice"

    def __post_init__(self) -> None:
        object.__setattr__(self, "criteria", MappingProxyType(dict(self.criteria)))


@dataclass(frozen=True, slots=True)
class SentScoreQuestion:
    instructions: str
    criteria: tuple[str, ...]
    type: Literal["score"] = "score"

    def __post_init__(self) -> None:
        object.__setattr__(self, "criteria", tuple(self.criteria))


@dataclass(frozen=True, slots=True)
class SentNoulQuestion:
    instructions: str
    type: Literal["noul"] = "noul"


SentQuestion = SentChoiceQuestion | SentScoreQuestion | SentNoulQuestion


@dataclass(frozen=True, slots=True)
class ChoiceAdoptionPolicy:
    min_confidence: float
    min_probability: float
    require_unique_maximum: Literal[True]


@dataclass(frozen=True, slots=True)
class ScoreAdoptionPolicy:
    required_impact_evidence: Literal["present"]
    min_confidence: float
    high_from: float


@dataclass(frozen=True, slots=True)
class NoulAdoptionPolicy:
    urgent_from: float
    not_urgent_through: float


@dataclass(frozen=True, slots=True)
class AdoptionPolicySnapshot:
    choice: ChoiceAdoptionPolicy
    score: ScoreAdoptionPolicy
    noul: NoulAdoptionPolicy
    version: Literal["text-judgment-adoption/1"] = "text-judgment-adoption/1"


@dataclass(frozen=True, slots=True)
class PolicyCheck:
    rule: (
        NormalizationReason
        | Literal["score_high_boundary", "noul_urgent_boundary", "noul_not_urgent_boundary"]
    )
    actual: float | str | bool
    operator: Literal["gte", "lte", "eq"]
    expected: float | str | bool
    passed: bool


@dataclass(frozen=True, slots=True)
class NormalizationDecision:
    status: Literal["eligible", "unmentioned", "needs_review"]
    reasons: tuple[NormalizationReason, ...]
    checks: tuple[PolicyCheck, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "checks", tuple(self.checks))


@dataclass(frozen=True, slots=True)
class JudgmentStateSnapshot:
    current_text: str
    question_id: QuestionId
    question_text: str
    confirmed: ConfirmedAnswers
    impact: Impact
    recent_user_texts: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "recent_user_texts", tuple(self.recent_user_texts))


@dataclass(frozen=True, slots=True)
class JudgmentInspection:
    state: JudgmentStateSnapshot
    questions: Mapping[JudgmentId, SentQuestion]
    policy: AdoptionPolicySnapshot
    normalization: Mapping[JudgmentId, NormalizationDecision]
    question_version: Literal["text-judgment-questions/2"] = "text-judgment-questions/2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "questions", MappingProxyType(dict(self.questions)))
        object.__setattr__(self, "normalization", MappingProxyType(dict(self.normalization)))


@dataclass(frozen=True, slots=True)
class JudgmentSuccess:
    """実際に送信した内容の記録と、検証済みの正規化記録を含むv2の成功応答。"""

    consultation_id: UUID
    request_id: UUID
    revision: int
    model: str
    evidence: JudgmentEvidence
    details: JudgmentDetails
    inspection: JudgmentInspection
    contract_version: Literal[2] = 2


JudgmentResponse = JudgmentSuccess


JudgmentResult = JudgmentSuccess | JudgmentFailure
