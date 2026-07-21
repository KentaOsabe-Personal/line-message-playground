from lineaccounts.friendship_repositories import (
    DjangoAccountProjectionRepository,
)
from linechannels.container import build_line_channel_directory

from .parsing import DefaultFriendshipEventParser
from .repositories import DjangoFriendshipAuditRepository
from .services import DefaultFriendshipSyncService


def build_friendship_sync_handler() -> DefaultFriendshipSyncService:
    return DefaultFriendshipSyncService(
        parser=DefaultFriendshipEventParser(),
        channel_directory=build_line_channel_directory(),
        account_repository=DjangoAccountProjectionRepository(),
        audit_repository=DjangoFriendshipAuditRepository(),
    )
