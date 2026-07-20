from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Protocol

from .types import HandlerOutcome, VerifiedWebhookEvent


class VerifiedEventHandler(Protocol):
    def handle(self, event: VerifiedWebhookEvent) -> HandlerOutcome: ...


class StaticHandlerRegistry:
    def __init__(
        self,
        registrations: Iterable[tuple[str, VerifiedEventHandler]] = (),
    ) -> None:
        handlers: dict[str, VerifiedEventHandler] = {}
        for event_type, handler in registrations:
            if event_type in handlers:
                raise ValueError("duplicate event type registration")
            handlers[event_type] = handler
        self._handlers = MappingProxyType(handlers)

    def resolve(self, event_type: str) -> VerifiedEventHandler | None:
        return self._handlers.get(event_type)
