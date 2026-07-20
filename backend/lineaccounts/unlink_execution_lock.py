"""全連携解除の LINE deauthorize を single-flight にする。"""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Literal, Protocol, runtime_checkable

from django.db import DatabaseError, connections


class UnlinkLockError(RuntimeError):
    def __init__(self, code: Literal["storage_unavailable"]) -> None:
        self.code = code
        super().__init__(code)


@runtime_checkable
class UnlinkExecutionLock(Protocol):
    def acquire(self, owner_slot: int) -> AbstractContextManager[bool]: ...


class MySQLUnlinkExecutionLock:
    def __init__(self, using: str = "default") -> None:
        self.using = using

    @contextmanager
    def acquire(self, owner_slot: int) -> Iterator[bool]:
        if owner_slot != 1:
            raise ValueError("invalid owner slot")
        connection = connections[self.using]
        lock_name = f"lineaccounts-unlink-owner-{owner_slot}-v1"
        acquired = False
        try:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT GET_LOCK(%s, 0)", [lock_name])
                    row = cursor.fetchone()
            except DatabaseError:
                raise UnlinkLockError("storage_unavailable") from None
            if row == (1,):
                acquired = True
            elif row != (0,):
                raise UnlinkLockError("storage_unavailable")
            yield acquired
        finally:
            if acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s)", [lock_name])
                        row = cursor.fetchone()
                except DatabaseError:
                    raise UnlinkLockError("storage_unavailable") from None
                if row != (1,):
                    raise UnlinkLockError("storage_unavailable")
