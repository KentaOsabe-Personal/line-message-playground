"""LINE account API の concrete dependency composition 境界。"""

from .gateway import HttpxLinePlatformGateway
from .recipient_services import DefaultRecipientService
from .repositories import DjangoAccountRepository
from .runtime import get_line_account_runtime, resolve_liff_linked_channel_policy
from .session_services import DefaultAccountSessionService
from .unlink_execution_lock import MySQLUnlinkExecutionLock
from .unlink_services import DefaultAccountUnlinkService
from linechannels.repositories import DjangoLineChannelDirectory


def build_session_service() -> DefaultAccountSessionService:
    runtime = get_line_account_runtime()
    return DefaultAccountSessionService(
        HttpxLinePlatformGateway(runtime),
        DjangoAccountRepository(),
        runtime.owner_eligibility,
    )


def build_recipient_service() -> DefaultRecipientService:
    runtime = get_line_account_runtime()
    directory = DjangoLineChannelDirectory()
    policy = resolve_liff_linked_channel_policy(runtime, directory)
    return DefaultRecipientService(
        directory,
        DjangoAccountRepository(),
        HttpxLinePlatformGateway(runtime),
        policy,
    )


def build_unlink_service() -> DefaultAccountUnlinkService:
    runtime = get_line_account_runtime()
    directory = DjangoLineChannelDirectory()
    resolve_liff_linked_channel_policy(runtime, directory)
    return DefaultAccountUnlinkService(
        HttpxLinePlatformGateway(runtime),
        DjangoAccountRepository(),
        MySQLUnlinkExecutionLock(),
        directory,
    )
