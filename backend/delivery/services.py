from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from .gateway import LINEGateway, LinePushAccepted, LinePushRejected, LinePushUnknown
from .formatters import FormattedMessage
from .models import DeliveryAttempt
from .types import (
    AcceptedDeliveryCommand,
    AcceptedLinkedAttempt,
    AttemptAccepted,
    AttemptConflict,
    ExistingAttempt,
    LinkedPushPreparation,
    LiveDeliveryTarget,
    SubmitLinkedDelivery,
    TargetUnavailable,
)


PROCESSING_TIMEOUT = timedelta(seconds=30)


class OperationIdReusedError(ValueError):
    pass


class DeliveryInProgressError(ValueError):
    pass


@dataclass(frozen=True)
class SubmitDeliveryCommand:
    operation_id: UUID
    message: FormattedMessage


@dataclass(frozen=True)
class ProcessingSubmission:
    operation_id: UUID
    status: Literal["processing"]
    accepted_at: datetime
    processing_expires_at: datetime
    created: bool


@dataclass(frozen=True)
class SucceededSubmission:
    operation_id: UUID
    status: Literal["succeeded"]
    accepted_at: datetime
    completed_at: datetime
    line_request_id: str | None
    line_accepted_request_id: str | None
    created: bool


@dataclass(frozen=True)
class FailedSubmission:
    operation_id: UUID
    status: Literal["failed"]
    accepted_at: datetime
    completed_at: datetime
    failure_type: str
    line_request_id: str | None
    created: bool


@dataclass(frozen=True)
class UnknownSubmission:
    operation_id: UUID
    status: Literal["unknown"]
    accepted_at: datetime
    completed_at: datetime
    failure_type: str
    line_request_id: str | None
    created: bool


DeliverySubmission = (
    ProcessingSubmission | SucceededSubmission | FailedSubmission | UnknownSubmission
)


class DeliveryService:
    def __init__(
        self,
        gateway=None,
        *,
        clock=timezone.now,
        target_directory=None,
        attempt_repository=None,
        receipt_capability_factory=None,
    ):
        self.gateway = gateway
        self.clock = clock
        self._target_directory = target_directory
        self._attempt_repository = attempt_repository
        self._receipt_capability_factory = receipt_capability_factory

    def submit(self, command):
        if self.gateway is None:
            self.gateway = LINEGateway()
        attempt, created = self._accept(command)
        if not created:
            return self._submission(attempt, created=False)

        gateway_result = self.gateway.push_text(
            command=self._line_command(command)
        )
        self._finalize(attempt.pk, gateway_result)
        attempt.refresh_from_db()
        return self._submission(attempt, created=True)

    def accept_confirmed(self, command):
        """確認済みlinked commandをlive targetへ再検証してacceptする。

        この段階ではcredential取得もLINE呼出しも行わない。raw receipt
        capabilityは、新規attemptを作成できた場合だけ次段へ返す。
        """

        if not isinstance(command, SubmitLinkedDelivery):
            raise ValueError("invalid linked delivery command")

        directory = self._linked_target_directory()
        confirmed = command.confirmation
        target = directory.resolve(
            confirmed.owner_identity.public_id,
            confirmed.channel_public_id,
            confirmed.recipient_public_id,
        )
        unavailable = self._validate_confirmed_target(target, confirmed)
        if unavailable is not None:
            return unavailable

        candidate = None
        if confirmed.receipt_requested:
            candidate = self._receipt_factory().create(
                confirmed.receipt_expires_at
            )

        from .repositories import build_request_fingerprint

        accepted_command = AcceptedDeliveryCommand(
            operation_id=command.operation_id,
            owner=confirmed.owner,
            owner_identity=confirmed.owner_identity,
            target=target.snapshot,
            message=command.message,
            request_fingerprint=build_request_fingerprint(
                owner=confirmed.owner,
                owner_identity=confirmed.owner_identity,
                channel_public_id=confirmed.channel_public_id,
                recipient_public_id=confirmed.recipient_public_id,
                message_fingerprint=command.message.fingerprint,
                receipt_requested=confirmed.receipt_requested,
            ),
            receipt_commitment=(
                candidate.commitment if candidate is not None else None
            ),
        )
        accept_result = self._linked_attempt_repository().accept(
            accepted_command
        )
        if isinstance(accept_result, AttemptAccepted):
            capability = (
                candidate.capability if candidate is not None else None
            )
            return AcceptedLinkedAttempt(
                attempt_id=accept_result.attempt_id,
                snapshot=accept_result.snapshot,
                push_preparation=LinkedPushPreparation(
                    target=target,
                    message=command.message,
                    receipt_capability=capability,
                ),
            )

        # 既存operation、active request競合、operation conflictでは、
        # candidateのraw値をどのresultにも含めず、このscopeで破棄する。
        candidate = None
        if isinstance(accept_result, (ExistingAttempt, AttemptConflict)):
            return accept_result
        raise ValueError("invalid attempt accept result")

    def check_status(self, operation_id):
        attempt = DeliveryAttempt.objects.filter(operation_id=operation_id).first()
        if attempt is None:
            return None
        now = self.clock()
        if (
            attempt.status == DeliveryAttempt.Status.PROCESSING
            and attempt.processing_expires_at <= now
        ):
            with transaction.atomic():
                DeliveryAttempt.objects.filter(
                    pk=attempt.pk,
                    status=DeliveryAttempt.Status.PROCESSING,
                ).update(
                    status=DeliveryAttempt.Status.UNKNOWN,
                    active_content_fingerprint=None,
                    failure_type=DeliveryAttempt.FailureType.PROCESSING_EXPIRED,
                    failed_at=now,
                    completed_at=now,
                )
            attempt.refresh_from_db()
        return self._submission(attempt, created=False)

    def _linked_target_directory(self):
        if self._target_directory is None:
            from lineaccounts.delivery_repositories import (
                DeliveryTargetDirectory,
            )

            self._target_directory = DeliveryTargetDirectory()
        return self._target_directory

    def _linked_attempt_repository(self):
        if self._attempt_repository is None:
            from .repositories import DjangoAttemptRepository

            self._attempt_repository = DjangoAttemptRepository(
                clock=self.clock
            )
        return self._attempt_repository

    def _receipt_factory(self):
        if self._receipt_capability_factory is None:
            from .receipt import ReceiptCapabilityFactory

            self._receipt_capability_factory = ReceiptCapabilityFactory()
        return self._receipt_capability_factory

    @staticmethod
    def _validate_confirmed_target(target, confirmed):
        if isinstance(target, TargetUnavailable):
            return target
        if not isinstance(target, LiveDeliveryTarget):
            return TargetUnavailable()
        if (
            target.owner_identity != confirmed.owner_identity
            or target.snapshot.channel_public_id
            != confirmed.channel_public_id
            or target.snapshot.recipient_public_id
            != confirmed.recipient_public_id
            or target.revision != confirmed.target_revision
        ):
            return TargetUnavailable()
        if target.delivery_available:
            return None
        if not target.snapshot.channel_active:
            return TargetUnavailable("channel_inactive")
        if not target.snapshot.recipient_enabled:
            return TargetUnavailable("recipient_disabled")
        if target.snapshot.friendship_state == "not_friend":
            return TargetUnavailable("not_friend")
        if target.snapshot.friendship_state == "unknown":
            return TargetUnavailable("friendship_unknown")
        return TargetUnavailable()

    @staticmethod
    def _line_command(command):
        from .gateway import LinePushCommand

        return LinePushCommand(
            retry_key=command.operation_id,
            text=command.message.formatted_text,
        )

    def _accept(self, command):
        existing = DeliveryAttempt.objects.filter(
            operation_id=command.operation_id
        ).first()
        if existing is not None:
            return self._classify_existing(existing, command.message.fingerprint)

        now = self.clock()
        try:
            with transaction.atomic():
                attempt = DeliveryAttempt.objects.create(
                    operation_id=command.operation_id,
                    subject=command.message.subject,
                    body=command.message.body,
                    formatted_text=command.message.formatted_text,
                    content_fingerprint=command.message.fingerprint,
                    active_content_fingerprint=command.message.fingerprint,
                    accepted_at=now,
                    processing_expires_at=now + PROCESSING_TIMEOUT,
                )
            return attempt, True
        except IntegrityError:
            existing = DeliveryAttempt.objects.filter(
                operation_id=command.operation_id
            ).first()
            if existing is not None:
                return self._classify_existing(existing, command.message.fingerprint)
            if DeliveryAttempt.objects.filter(
                active_content_fingerprint=command.message.fingerprint
            ).exists():
                raise DeliveryInProgressError("delivery_in_progress")
            raise

    @staticmethod
    def _classify_existing(attempt, fingerprint):
        if attempt.content_fingerprint != fingerprint:
            raise OperationIdReusedError("operation_id_reused")
        return attempt, False

    def _finalize(self, attempt_id, gateway_result):
        completed_at = self.clock()
        values = {
            "active_content_fingerprint": None,
            "completed_at": completed_at,
        }
        if isinstance(gateway_result, LinePushAccepted):
            values.update(
                status=DeliveryAttempt.Status.SUCCEEDED,
                sent_at=completed_at,
                line_request_id=gateway_result.request_id,
                line_accepted_request_id=gateway_result.accepted_request_id,
            )
        elif isinstance(gateway_result, LinePushUnknown):
            values.update(
                status=DeliveryAttempt.Status.UNKNOWN,
                failure_type=gateway_result.failure_type,
                failed_at=completed_at,
            )
        elif isinstance(gateway_result, LinePushRejected):
            values.update(
                status=DeliveryAttempt.Status.FAILED,
                failure_type=gateway_result.failure_type,
                failed_at=completed_at,
            )
        else:
            values.update(
                status=DeliveryAttempt.Status.FAILED,
                failure_type=DeliveryAttempt.FailureType.UNEXPECTED,
                failed_at=completed_at,
            )
        with transaction.atomic():
            DeliveryAttempt.objects.filter(
                pk=attempt_id,
                status=DeliveryAttempt.Status.PROCESSING,
            ).update(**values)

    @staticmethod
    def _submission(attempt, *, created):
        common = {
            "operation_id": attempt.operation_id,
            "status": attempt.status,
            "accepted_at": attempt.accepted_at,
            "created": created,
        }
        if attempt.status == DeliveryAttempt.Status.PROCESSING:
            return ProcessingSubmission(
                **common,
                processing_expires_at=attempt.processing_expires_at,
            )
        if attempt.status == DeliveryAttempt.Status.SUCCEEDED:
            return SucceededSubmission(
                **common,
                completed_at=attempt.completed_at,
                line_request_id=attempt.line_request_id,
                line_accepted_request_id=attempt.line_accepted_request_id,
            )
        submission_type = (
            UnknownSubmission
            if attempt.status == DeliveryAttempt.Status.UNKNOWN
            else FailedSubmission
        )
        return submission_type(
            **common,
            completed_at=attempt.completed_at,
            failure_type=attempt.failure_type,
            line_request_id=attempt.line_request_id,
        )
