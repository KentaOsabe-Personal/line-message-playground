from collections.abc import Callable, Iterable

from lineaccounts.interaction_repositories import (
    DjangoInteractionAccountDirectory,
)
from linechannels.container import (
    build_credential_repository,
    build_line_channel_directory,
)
from linewebhooks.handlers import VerifiedEventHandler

from .gateways import HttpxLineReplyGateway
from .parsing import DefaultInteractionParser
from .registries import StaticCommandRegistry, StaticPostbackActionRegistry
from .repositories import DjangoInteractionAuditRepository
from .services import DefaultInteractionService
from .types import PostbackActionHandler


def build_interaction_handler(
    *,
    action_registrations: Iterable[tuple[str, PostbackActionHandler]] = (),
    monotonic_clock: Callable[[], float],
) -> VerifiedEventHandler:
    action_registry = StaticPostbackActionRegistry(action_registrations)
    return DefaultInteractionService(
        parser=DefaultInteractionParser(),
        channel_directory=build_line_channel_directory(),
        account_directory=DjangoInteractionAccountDirectory(),
        command_registry=StaticCommandRegistry(),
        action_registry=action_registry,
        credential_repository=build_credential_repository(),
        reply_gateway=HttpxLineReplyGateway(),
        audit_repository=DjangoInteractionAuditRepository(),
        monotonic=monotonic_clock,
    )
