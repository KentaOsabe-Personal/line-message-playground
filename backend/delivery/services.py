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
    DeliveryPrePushFailure,
    ExistingAttempt,
    LinkedPushExecuted,
    LinkedPushPrevented,
    LinkedPushStored,
    LinkedPushPreparation,
    LiveDeliveryTarget,
    PushLinkedRecipientCommand,
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
        credential_repository=None,
        channel_push_gateway=None,
    ):
        self.gateway = gateway
        self.clock = clock
        self._target_directory = target_directory
        self._attempt_repository = attempt_repository
        self._receipt_capability_factory = receipt_capability_factory
        self._credential_repository = credential_repository
        self._channel_push_gateway = channel_push_gateway

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

    def push_accepted(self, accepted):
        """新規linked attemptだけをcredential再検証後に最大一回pushする。"""

        if not isinstance(accepted, AcceptedLinkedAttempt):
            raise ValueError("invalid accepted linked attempt")

        preparation = accepted.push_preparation
        selected_channel_id = (
            preparation.target.snapshot.channel_public_id
        )
        credential = self._linked_credential_repository().get_access_token(
            selected_channel_id
        )

        from linechannels.types import (
            AccessToken,
            CredentialAvailable,
            CredentialUnavailable,
        )

        if isinstance(credential, CredentialUnavailable):
            return self._prevent_linked_push(
                accepted.attempt_id,
                "configuration",
            )
        if not isinstance(credential, CredentialAvailable) or not isinstance(
            credential.value,
            AccessToken,
        ):
            raise ValueError("invalid access token credential result")

        expected_target = preparation.target
        current_target = self._linked_target_directory().resolve(
            expected_target.owner_identity.public_id,
            expected_target.snapshot.channel_public_id,
            expected_target.snapshot.recipient_public_id,
        )
        if not self._same_push_target(current_target, expected_target):
            return self._prevent_linked_push(
                accepted.attempt_id,
                "target_changed",
            )

        result = self._linked_channel_push_gateway().push(
            PushLinkedRecipientCommand(
                operation_id=accepted.snapshot.operation_id,
                access_token=credential.value,
                subject=current_target.subject,
                text=preparation.message.formatted_text,
                receipt_capability=preparation.receipt_capability,
            )
        )
        return LinkedPushExecuted(
            attempt_id=accepted.attempt_id,
            result=result,
        )

    def finalize_linked_push(self, performed):
        """gateway実行結果をfirst-terminal CASで保存結果へ収束させる。

        この境界は外部依存を再解決せず、実行済み結果を一度repositoryへ
        渡すだけに限定する。競合時もrepositoryが返す先行終端状態を採用する。
        """

        if not isinstance(performed, LinkedPushExecuted):
            raise ValueError("invalid linked push execution result")
        snapshot = self._linked_attempt_repository().finalize(
            performed.attempt_id,
            performed.result,
            self.clock(),
        )
        return LinkedPushStored(snapshot=snapshot)

    def check_linked_status(
        self,
        owner_principal_slot,
        operation_id,
    ):
        """owner scopeで保存済み状態を取得し、期限判定をrepositoryへ委譲する。"""

        return self._linked_attempt_repository().get_for_owner(
            owner_principal_slot,
            operation_id,
        )

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

    def _linked_credential_repository(self):
        if self._credential_repository is None:
            from linechannels.container import build_credential_repository

            self._credential_repository = build_credential_repository()
        return self._credential_repository

    def _linked_channel_push_gateway(self):
        if self._channel_push_gateway is None:
            from .gateway import LINEChannelPushGateway

            self._channel_push_gateway = LINEChannelPushGateway()
        return self._channel_push_gateway

    def _prevent_linked_push(self, attempt_id, failure_type):
        failure = DeliveryPrePushFailure(failure_type)
        snapshot = self._linked_attempt_repository().finalize(
            attempt_id,
            failure,
            self.clock(),
        )
        if (
            snapshot.status == "failed"
            and snapshot.failure == failure.failure_type
        ):
            return LinkedPushPrevented(
                snapshot=snapshot,
                failure_type=failure.failure_type,
            )
        return LinkedPushStored(snapshot=snapshot)

    @staticmethod
    def _same_push_target(current, expected):
        return (
            isinstance(current, LiveDeliveryTarget)
            and current.delivery_available
            and current.owner_identity == expected.owner_identity
            and current.provider_id == expected.provider_id
            and current.snapshot.channel_public_id
            == expected.snapshot.channel_public_id
            and current.snapshot.recipient_public_id
            == expected.snapshot.recipient_public_id
            and current.revision == expected.revision
        )

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
                    owner_principal_slot=1,
                    subject=command.message.subject,
                    body=command.message.body,
                    formatted_text=command.message.formatted_text,
                    content_fingerprint=command.message.fingerprint,
                    active_content_fingerprint=command.message.fingerprint,
                    request_fingerprint=command.message.fingerprint,
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
