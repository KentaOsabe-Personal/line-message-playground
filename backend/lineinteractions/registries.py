import re
from collections.abc import Iterable
from types import MappingProxyType

from .constants import (
    CONNECTIVITY_COMMAND_IDENTIFIER,
    CONNECTIVITY_COMMAND_TEXT,
    CONNECTIVITY_REPLY_TEXT,
)
from .types import (
    CommandDefinition,
    PostbackActionHandler,
)


_ACTION_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")


class StaticCommandRegistry:
    def __init__(self) -> None:
        self._command = CommandDefinition(
            identifier=CONNECTIVITY_COMMAND_IDENTIFIER,
            exact_text=CONNECTIVITY_COMMAND_TEXT,
            reply_text=CONNECTIVITY_REPLY_TEXT,
        )

    def resolve(self, candidate: str) -> CommandDefinition | None:
        return self._command if candidate == self._command.exact_text else None


class StaticPostbackActionRegistry:
    def __init__(
        self,
        registrations: Iterable[tuple[str, PostbackActionHandler]] = (),
    ) -> None:
        handlers: dict[str, PostbackActionHandler] = {}
        for action_name, handler in registrations:
            if (
                not isinstance(action_name, str)
                or _ACTION_NAME.fullmatch(action_name) is None
                or not callable(getattr(handler, "handle", None))
                or action_name in handlers
            ):
                raise ValueError("invalid action registration")
            handlers[action_name] = handler
        self._handlers = MappingProxyType(handlers)

    def resolve(self, action_name: str) -> PostbackActionHandler | None:
        return self._handlers.get(action_name)
