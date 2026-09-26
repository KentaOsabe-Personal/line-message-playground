from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Literal


_WINDOW_SECONDS = 60.0
_MAX_STARTS = 10


@dataclass(frozen=True, slots=True)
class LabLimitRejected:
    reason: Literal["concurrent", "rate"]


@dataclass(slots=True)
class _OwnerState:
    starts: deque[float] = field(default_factory=deque)
    running: bool = False


class LabLimitPermit:
    def __init__(self, limits: "LabLimits", owner_digest: str) -> None:
        self._limits = limits
        self._owner_digest = owner_digest
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._limits._release(self._owner_digest)
        self._released = True

    def __enter__(self) -> "LabLimitPermit":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


class LabLimits:
    def __init__(self, *, monotonic_clock: Callable[[], float] = monotonic) -> None:
        self._monotonic = monotonic_clock
        self._mutex = Lock()
        self._owners: dict[str, _OwnerState] = {}

    def acquire(self, owner_digest: str) -> LabLimitPermit | LabLimitRejected:
        now = self._monotonic()
        with self._mutex:
            state = self._owners.setdefault(owner_digest, _OwnerState())
            cutoff = now - _WINDOW_SECONDS
            while state.starts and state.starts[0] <= cutoff:
                state.starts.popleft()
            if state.running:
                return LabLimitRejected("concurrent")
            if len(state.starts) >= _MAX_STARTS:
                return LabLimitRejected("rate")
            state.starts.append(now)
            state.running = True
            return LabLimitPermit(self, owner_digest)

    def _release(self, owner_digest: str) -> None:
        with self._mutex:
            state = self._owners.get(owner_digest)
            if state is not None:
                state.running = False
