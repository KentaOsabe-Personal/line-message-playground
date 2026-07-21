from typing import Literal

from django.db import DatabaseError, OperationalError, transaction

from .models import FriendshipSyncAudit
from .types import FriendshipAuditRecord


class FriendshipAuditStorageError(RuntimeError):
    def __init__(
        self, code: Literal["retryable", "storage_unavailable"]
    ) -> None:
        self.code = code
        super().__init__(code)


class FriendshipAuditProgrammingError(RuntimeError):
    pass


class DjangoFriendshipAuditRepository:
    _RETRYABLE_DATABASE_CODES = frozenset((1205, 1213))

    def __init__(self, using: str = "default") -> None:
        self.using = using

    def record(self, audit: FriendshipAuditRecord) -> None:
        if not transaction.get_connection(self.using).in_atomic_block:
            raise FriendshipAuditProgrammingError("transaction_required")
        if not isinstance(audit, FriendshipAuditRecord):
            raise FriendshipAuditProgrammingError("invalid_audit")

        try:
            FriendshipSyncAudit.objects.using(self.using).create(
                channel_public_id=audit.channel_public_id,
                webhook_event_id=audit.webhook_event_id,
                event_type=audit.event_type,
                occurred_at_ms=audit.occurred_at_ms,
                outcome=audit.outcome,
                is_unblocked=audit.is_unblocked,
            )
        except OperationalError as error:
            code = error.args[0] if error.args else None
            if code in self._RETRYABLE_DATABASE_CODES:
                raise FriendshipAuditStorageError("retryable") from None
            raise FriendshipAuditStorageError("storage_unavailable") from None
        except DatabaseError:
            raise FriendshipAuditStorageError("storage_unavailable") from None
