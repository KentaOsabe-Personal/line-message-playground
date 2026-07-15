from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Literal, Protocol, runtime_checkable

from django.db import DatabaseError, connections


class RotationLockError(RuntimeError):
    def __init__(self, code: Literal["storage_unavailable"]) -> None:
        self.code = code
        super().__init__(code)


@runtime_checkable
class RotationLock(Protocol):
    def acquire(self) -> AbstractContextManager[bool]: ...


class MySQLRotationLock:
    __LOCK_NAME = "linechannels-credential-rotation-v1"

    def __init__(self, using: str = "default") -> None:
        self.using = using

    @contextmanager
    def acquire(self) -> Iterator[bool]:
        connection = connections[self.using]
        acquired: bool | None = None
        try:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT GET_LOCK(%s, 0)", [self.__LOCK_NAME])
                    row = cursor.fetchone()
                if row == (1,):
                    acquired = True
                elif row == (0,):
                    acquired = False
                else:
                    raise RotationLockError("storage_unavailable")
            except DatabaseError:
                raise RotationLockError("storage_unavailable") from None

            yield acquired
        finally:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", [self.__LOCK_NAME])
                    release_row = cursor.fetchone()
                if acquired is True and release_row != (1,):
                    raise RotationLockError("storage_unavailable")
                if acquired is False and release_row != (0,):
                    raise RotationLockError("storage_unavailable")
            except DatabaseError:
                raise RotationLockError("storage_unavailable") from None
