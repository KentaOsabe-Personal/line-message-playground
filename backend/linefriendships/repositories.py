from typing import Literal
from threading import Lock
from weakref import WeakSet, ref

from django.db import DatabaseError, OperationalError, transaction

from linechannels.reference_fence import (
    ChannelReferenceFence,
)

from .models import FriendshipSyncAudit
from .types import FriendshipAuditRecord


class _LockedFriendshipReference:
    __slots__ = (
        "channel_public_id",
        "issuer",
        "connection",
        "outermost_atomic",
        "transaction_invalidator",
        "__weakref__",
    )

    def __init__(
        self,
        *,
        channel_public_id,
        issuer,
        connection,
        outermost_atomic,
    ) -> None:
        self.channel_public_id = channel_public_id
        self.issuer = issuer
        self.connection = connection
        self.outermost_atomic = outermost_atomic
        self.transaction_invalidator = None


class FriendshipAuditStorageError(RuntimeError):
    def __init__(
        self, code: Literal["channel_not_found", "retryable", "storage_unavailable"]
    ) -> None:
        self.code = code
        super().__init__(code)


class FriendshipAuditProgrammingError(RuntimeError):
    pass


class DjangoFriendshipAuditRepository:
    _RETRYABLE_DATABASE_CODES = frozenset((1205, 1213))

    def __init__(
        self,
        reference_fence: ChannelReferenceFence,
        *,
        using: str = "default",
    ) -> None:
        self.using = using
        self._reference_fence = reference_fence
        self._capability_issuer = object()
        self._issued_capabilities: WeakSet[_LockedFriendshipReference] = WeakSet()
        self._capability_lock = Lock()

    def record(self, audit: FriendshipAuditRecord) -> None:
        if not transaction.get_connection(self.using).in_atomic_block:
            raise FriendshipAuditProgrammingError("transaction_required")
        if not isinstance(audit, FriendshipAuditRecord):
            raise FriendshipAuditProgrammingError("invalid_audit")

        try:
            locked = self.lock_reference(audit.channel_public_id)
            self.record_after_fence(audit, locked)
        except OperationalError as error:
            code = error.args[0] if error.args else None
            if code in self._RETRYABLE_DATABASE_CODES:
                raise FriendshipAuditStorageError("retryable") from None
            raise FriendshipAuditStorageError("storage_unavailable") from None
        except DatabaseError:
            raise FriendshipAuditStorageError("storage_unavailable") from None

    def lock_reference(self, channel_public_id) -> object:
        fence_result = self._reference_fence.lock_existing(channel_public_id)
        if fence_result.status == "channel_not_found":
            raise FriendshipAuditStorageError("channel_not_found")
        if fence_result.status == "storage_retryable":
            raise FriendshipAuditStorageError("retryable")
        if fence_result.status == "storage_unavailable":
            raise FriendshipAuditStorageError("storage_unavailable")
        if fence_result.status != "locked":
            raise FriendshipAuditProgrammingError("invalid_fence_result")
        connection = transaction.get_connection(self.using)
        if not connection.in_atomic_block or not connection.atomic_blocks:
            raise FriendshipAuditProgrammingError("transaction_required")
        capability = _LockedFriendshipReference(
            channel_public_id=channel_public_id,
            issuer=self._capability_issuer,
            connection=connection,
            outermost_atomic=connection.atomic_blocks[0],
        )
        capability_ref = ref(capability)

        def invalidate_after_transaction() -> None:
            issued = capability_ref()
            if issued is not None:
                with self._capability_lock:
                    self._issued_capabilities.discard(issued)

        capability.transaction_invalidator = invalidate_after_transaction
        with self._capability_lock:
            self._issued_capabilities.add(capability)
        transaction.on_commit(invalidate_after_transaction, using=self.using)
        return capability

    def record_after_fence(
        self,
        audit: FriendshipAuditRecord,
        locked: object,
    ) -> None:
        connection = transaction.get_connection(self.using)
        if not connection.in_atomic_block or not connection.atomic_blocks:
            raise FriendshipAuditProgrammingError("transaction_required")
        valid = False
        with self._capability_lock:
            if (
                type(locked) is _LockedFriendshipReference
                and locked in self._issued_capabilities
                and locked.issuer is self._capability_issuer
                and locked.connection is connection
                and locked.outermost_atomic is connection.atomic_blocks[0]
                and any(
                    entry[1] is locked.transaction_invalidator
                    for entry in connection.run_on_commit
                )
                and locked.channel_public_id == audit.channel_public_id
            ):
                self._issued_capabilities.discard(locked)
                valid = True
            elif type(locked) is _LockedFriendshipReference:
                self._issued_capabilities.discard(locked)
        if not valid:
            raise FriendshipAuditProgrammingError("invalid_reference_lock")
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


class DjangoFriendshipReferenceProbe:
    def __init__(self, using: str = "default") -> None:
        self.using = using

    def is_referenced(self, channel_public_id) -> bool:
        return FriendshipSyncAudit.objects.using(self.using).filter(
            channel_public_id=channel_public_id
        ).exists()
