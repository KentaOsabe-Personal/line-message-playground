from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from django.db import transaction

from .repositories import PersistenceError
from .rotation_item import CredentialRotationItemProcessor
from .rotation_lock import RotationLock
from .rotation_repository import RotationCredentialRepository


RotationFailureCode = Literal[
    "credential_missing",
    "credential_unreadable",
    "verification_failed",
    "retryable",
    "storage_unavailable",
]


@dataclass(frozen=True)
class RotationItemFailure:
    channel_public_id: UUID
    code: RotationFailureCode


@dataclass(frozen=True)
class RotationSummary:
    status: Literal["complete", "incomplete", "busy", "configuration_required"]
    verified_count: int
    rotated_count: int
    failed_count: int
    failures: tuple[RotationItemFailure, ...]
    old_keys_removable: bool


class _RotationReadiness(Protocol):
    def rotation_readiness(self) -> Literal["ready", "old_key_missing"]: ...


@runtime_checkable
class CredentialRotationService(Protocol):
    def rotate_all(self) -> RotationSummary: ...


class DefaultCredentialRotationService:
    def __init__(
        self,
        cipher: _RotationReadiness,
        repository: RotationCredentialRepository,
        rotation_lock: RotationLock,
        item_processor: CredentialRotationItemProcessor,
        *,
        using: str = "default",
    ) -> None:
        self._cipher = cipher
        self._repository = repository
        self._rotation_lock = rotation_lock
        self._item_processor = item_processor
        self._using = using

    def rotate_all(self) -> RotationSummary:
        if self._cipher.rotation_readiness() != "ready":
            return self._empty_summary("configuration_required")

        with self._rotation_lock.acquire() as acquired:
            if not acquired:
                return self._empty_summary("busy")

            verified_count = 0
            rotated_count = 0
            failures: dict[UUID, RotationFailureCode] = {}
            for public_id in self._repository.list_credential_public_ids():
                outcome, failure = self._process_row(public_id)
                if outcome == "verified":
                    verified_count += 1
                elif outcome == "rotated":
                    rotated_count += 1
                elif failure is not None:
                    failures[public_id] = failure

            for public_id in self._repository.list_credential_public_ids():
                failure = self._verify_row(public_id)
                if failure is not None:
                    failures.setdefault(public_id, failure)

            failure_results = tuple(
                RotationItemFailure(public_id, code)
                for public_id, code in failures.items()
            )
            complete = not failure_results
            return RotationSummary(
                status="complete" if complete else "incomplete",
                verified_count=verified_count,
                rotated_count=rotated_count,
                failed_count=len(failure_results),
                failures=failure_results,
                old_keys_removable=complete,
            )

    def _process_row(
        self, public_id: UUID
    ) -> tuple[Literal["verified", "rotated", "failed"], RotationFailureCode | None]:
        try:
            with transaction.atomic(using=self._using):
                credentials = self._repository.get_credentials_for_update(public_id)
                if credentials is None:
                    transaction.set_rollback(True, using=self._using)
                    return "failed", "credential_missing"

                result = self._item_processor.process(public_id, credentials)
                if result.status == "verified":
                    return "verified", None
                if result.status == "rotated":
                    self._repository.replace_credentials_locked(
                        public_id, result.credentials
                    )
                    return "rotated", None
                transaction.set_rollback(True, using=self._using)
                return "failed", result.code
        except PersistenceError as error:
            return "failed", self._persistence_failure(error)
        except Exception:
            return "failed", "storage_unavailable"

    def _verify_row(self, public_id: UUID) -> RotationFailureCode | None:
        try:
            with transaction.atomic(using=self._using):
                credentials = self._repository.get_credentials_for_update(public_id)
                if credentials is None:
                    return "credential_missing"
                result = self._item_processor.verify_with_primary(
                    public_id, credentials
                )
                if result.status == "verified":
                    return None
                return result.code
        except PersistenceError as error:
            return self._persistence_failure(error)
        except Exception:
            return "storage_unavailable"

    @staticmethod
    def _persistence_failure(error: PersistenceError) -> RotationFailureCode:
        return {
            "unique_conflict": "storage_unavailable",
            "retryable": "retryable",
            "storage_unavailable": "storage_unavailable",
            "credentials_incomplete": "credential_missing",
        }[error.code]

    @staticmethod
    def _empty_summary(
        status: Literal["busy", "configuration_required"],
    ) -> RotationSummary:
        return RotationSummary(
            status=status,
            verified_count=0,
            rotated_count=0,
            failed_count=0,
            failures=(),
            old_keys_removable=False,
        )
