from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from .gateway import LINEGateway, LinePushAccepted, LinePushRejected, LinePushUnknown
from .formatters import FormattedMessage
from .models import DeliveryAttempt


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
    def __init__(self, gateway=None, *, clock=timezone.now):
        self.gateway = gateway or LINEGateway()
        self.clock = clock

    def submit(self, command):
        attempt, created = self._accept(command)
        if not created:
            return self._submission(attempt, created=False)

        gateway_result = self.gateway.push_text(
            command=self._line_command(command)
        )
        self._finalize(attempt.pk, gateway_result)
        attempt.refresh_from_db()
        return self._submission(attempt, created=True)

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
