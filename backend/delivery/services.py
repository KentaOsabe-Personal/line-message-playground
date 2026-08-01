from datetime import datetime
from typing import Callable, Protocol
from uuid import UUID

from django.utils import timezone
from django.db import DatabaseError

from linechannels.types import (
    AccessToken,
    CredentialAvailable,
    CredentialUnavailable,
)

from .repositories import AttemptRepository, build_request_fingerprint
from .types import (
    AcceptedDeliveryCommand,
    AcceptedLinkedAttempt,
    AttemptAccepted,
    AttemptConflict,
    AttemptStorageFailed,
    AttemptTargetUnavailable,
    DeliveryPrePushFailure,
    ExistingAttempt,
    LinkedPushExecuted,
    LinkedPushPrevented,
    LinkedPushStored,
    LinkedPushPreparation,
    LinePushAccepted,
    LinePushRejected,
    LinePushUnknown,
    LiveDeliveryTarget,
    PushLinkedRecipientCommand,
    ReceiptCapabilityCandidate,
    SubmitLinkedDelivery,
    TargetUnavailable,
)


class TargetDirectory(Protocol):
    def resolve(
        self,
        owner_identity_id: UUID,
        channel_id: UUID,
        recipient_id: UUID,
    ) -> LiveDeliveryTarget | TargetUnavailable: ...


class ReceiptCapabilityFactoryPort(Protocol):
    def create(
        self,
        confirmed_expires_at: datetime,
    ) -> ReceiptCapabilityCandidate: ...


class CredentialRepositoryPort(Protocol):
    def get_access_token(
        self,
        channel_public_id: UUID,
    ) -> CredentialAvailable[AccessToken] | CredentialUnavailable: ...


class ChannelPushGatewayPort(Protocol):
    def push(
        self,
        command: PushLinkedRecipientCommand,
    ) -> LinePushAccepted | LinePushRejected | LinePushUnknown: ...


class DeliveryService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = timezone.now,
        target_directory: TargetDirectory | None = None,
        attempt_repository: AttemptRepository | None = None,
        receipt_capability_factory: ReceiptCapabilityFactoryPort | None = None,
        credential_repository: CredentialRepositoryPort | None = None,
        channel_push_gateway: ChannelPushGatewayPort | None = None,
    ):
        self.clock = clock
        self._target_directory = target_directory
        self._attempt_repository = attempt_repository
        self._receipt_capability_factory = receipt_capability_factory
        self._credential_repository = credential_repository
        self._channel_push_gateway = channel_push_gateway

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
        if isinstance(accept_result, AttemptTargetUnavailable):
            return TargetUnavailable("target_not_available")
        if isinstance(accept_result, AttemptStorageFailed):
            raise DatabaseError("storage_unavailable")
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
        owner_principal_slot: int,
        operation_id: UUID,
    ):
        """owner scopeで保存済み状態を取得し、期限判定をrepositoryへ委譲する。"""

        return self._linked_attempt_repository().get_for_owner(
            owner_principal_slot,
            operation_id,
        )

    def _linked_target_directory(self):
        if self._target_directory is None:
            raise RuntimeError(
                "target directory dependency is not configured"
            )
        return self._target_directory

    def _linked_attempt_repository(self):
        if self._attempt_repository is None:
            raise RuntimeError(
                "attempt repository dependency is not configured"
            )
        return self._attempt_repository

    def _receipt_factory(self):
        if self._receipt_capability_factory is None:
            raise RuntimeError(
                "receipt capability factory dependency is not configured"
            )
        return self._receipt_capability_factory

    def _linked_credential_repository(self):
        if self._credential_repository is None:
            raise RuntimeError(
                "credential repository dependency is not configured"
            )
        return self._credential_repository

    def _linked_channel_push_gateway(self):
        if self._channel_push_gateway is None:
            raise RuntimeError(
                "channel push gateway dependency is not configured"
            )
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
