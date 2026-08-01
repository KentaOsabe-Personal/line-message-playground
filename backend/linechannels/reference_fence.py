from dataclasses import dataclass
from collections.abc import Iterable
from typing import Literal, Protocol, TypeAlias, runtime_checkable
from uuid import UUID

from django.db import DatabaseError, OperationalError, transaction

from .models import LineChannel
from .repositories import RepositoryProgrammingError


ReferenceFenceStatus: TypeAlias = Literal[
    "locked",
    "channel_not_found",
    "storage_retryable",
    "storage_unavailable",
]


@dataclass(frozen=True, slots=True)
class ReferenceFenceResult:
    status: ReferenceFenceStatus

    def __post_init__(self) -> None:
        if self.status not in {
            "locked",
            "channel_not_found",
            "storage_retryable",
            "storage_unavailable",
        }:
            raise ValueError("invalid reference fence result")


@runtime_checkable
class ChannelReferenceFence(Protocol):
    def lock_existing(self, channel_public_id: UUID) -> ReferenceFenceResult: ...


@runtime_checkable
class ChannelReferenceProbe(Protocol):
    def is_referenced(self, channel_public_id: UUID) -> bool: ...


ReferenceCheckStatus: TypeAlias = Literal[
    "referenced",
    "unreferenced",
    "storage_retryable",
    "storage_unavailable",
]


@dataclass(frozen=True, slots=True)
class ReferenceCheckResult:
    status: ReferenceCheckStatus

    def __post_init__(self) -> None:
        if self.status not in {
            "referenced",
            "unreferenced",
            "storage_retryable",
            "storage_unavailable",
        }:
            raise ValueError("invalid reference check result")


class ChannelReferenceDirectory:
    _RETRYABLE_DATABASE_CODES = frozenset((1205, 1213))

    def __init__(self, probes: Iterable[ChannelReferenceProbe]) -> None:
        self._probes = tuple(probes)

    def is_referenced(self, channel_public_id: UUID) -> ReferenceCheckResult:
        if not isinstance(channel_public_id, UUID):
            raise RepositoryProgrammingError("invalid_channel_public_id")
        try:
            for probe in self._probes:
                if probe.is_referenced(channel_public_id):
                    return ReferenceCheckResult("referenced")
        except OperationalError as error:
            code = error.args[0] if error.args else None
            return ReferenceCheckResult(
                "storage_retryable"
                if code in self._RETRYABLE_DATABASE_CODES
                else "storage_unavailable"
            )
        except DatabaseError:
            return ReferenceCheckResult("storage_unavailable")
        return ReferenceCheckResult("unreferenced")


class DjangoChannelReferenceFence:
    _RETRYABLE_DATABASE_CODES = frozenset((1205, 1213))

    def __init__(self, *, using: str = "default") -> None:
        self.using = using

    def lock_existing(self, channel_public_id: UUID) -> ReferenceFenceResult:
        if not transaction.get_connection(self.using).in_atomic_block:
            raise RepositoryProgrammingError("transaction_required")
        if not isinstance(channel_public_id, UUID):
            raise RepositoryProgrammingError("invalid_channel_public_id")
        try:
            row = (
                LineChannel.objects.using(self.using)
                .select_for_update()
                .filter(public_id=channel_public_id)
                .values("public_id")
                .first()
            )
        except OperationalError as error:
            code = error.args[0] if error.args else None
            return ReferenceFenceResult(
                "storage_retryable"
                if code in self._RETRYABLE_DATABASE_CODES
                else "storage_unavailable"
            )
        except DatabaseError:
            return ReferenceFenceResult("storage_unavailable")
        return ReferenceFenceResult("locked" if row is not None else "channel_not_found")
