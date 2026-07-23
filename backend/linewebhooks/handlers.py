from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Protocol

from .types import (
    HandlerExecutionContext,
    HandlerOutcome,
    HandlerRegistration,
    VerifiedWebhookEvent,
)


class VerifiedEventHandler(Protocol):
    def handle(
        self,
        event: VerifiedWebhookEvent,
        context: HandlerExecutionContext,
    ) -> HandlerOutcome: ...


class StaticHandlerRegistry:
    def __init__(
        self,
        registrations: Iterable[HandlerRegistration] = (),
    ) -> None:
        handlers: dict[str, HandlerRegistration] = {}
        for registration in registrations:
            if not isinstance(registration, HandlerRegistration):
                raise TypeError("handler registration is required")
            if registration.event_type in handlers:
                raise ValueError("duplicate event type registration")
            handlers[registration.event_type] = registration
        self._handlers = MappingProxyType(handlers)

    def resolve(self, event_type: str) -> HandlerRegistration | None:
        return self._handlers.get(event_type)
