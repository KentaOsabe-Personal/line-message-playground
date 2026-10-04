from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


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
class JudgmentRequest:
    text: str


@dataclass(frozen=True, slots=True)
class JudgmentSuccess:
    model: str
    answers: dict[str, object]
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class JudgmentFailure:
    code: Literal[
        "rate_limited", "access_expired", "judge_unavailable", "judge_timeout", "unexpected"
    ]
