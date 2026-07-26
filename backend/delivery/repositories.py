from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Callable, Protocol
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import DeliveryAttempt
from .types import (
    AcceptedDeliveryCommand,
    AttemptAcceptResult,
    AttemptAccepted,
    AttemptConflict,
    DeliverySnapshot,
    ExistingAttempt,
    FixedTargetSnapshot,
    LinkedTargetSnapshot,
    LinePushAccepted,
    LinePushRejected,
    LinePushResult,
    LinePushUnknown,
    MessageSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
    RequestFingerprint,
)


PROCESSING_TIMEOUT = timedelta(seconds=30)
_REQUEST_FINGERPRINT_VERSION = "v1"


class AttemptRepository(Protocol):
    def accept(
        self,
        command: AcceptedDeliveryCommand,
    ) -> AttemptAcceptResult: ...

    def finalize(
        self,
        attempt_id: int,
        result: LinePushResult,
        completed_at: datetime,
    ) -> DeliverySnapshot: ...

    def get_for_owner(
        self,
        owner_principal_slot: int,
        operation_id: UUID,
    ) -> DeliverySnapshot | None: ...


def build_request_fingerprint(
    *,
    owner: OwnerPrincipal,
    owner_identity: OwnerIdentitySnapshot,
    channel_public_id: UUID,
    recipient_public_id: UUID,
    message_fingerprint: str,
    receipt_requested: bool,
) -> RequestFingerprint:
    """配信request identityを型付きのversioned SHA-256へ縮約する。"""

    if not isinstance(owner, OwnerPrincipal):
        raise ValueError("invalid owner principal")
    if not isinstance(owner_identity, OwnerIdentitySnapshot):
        raise ValueError("invalid owner identity")
    if not isinstance(channel_public_id, UUID):
        raise ValueError("invalid channel public ID")
    if not isinstance(recipient_public_id, UUID):
        raise ValueError("invalid recipient public ID")
    RequestFingerprint(message_fingerprint)
    if type(receipt_requested) is not bool:
        raise ValueError("invalid receipt requested flag")

    fields = (
        ("kind", "linked-recipient-delivery-request"),
        ("version", _REQUEST_FINGERPRINT_VERSION),
        ("owner-principal-slot", str(owner.slot)),
        ("owner-identity-public-id", str(owner_identity.public_id)),
        ("channel-public-id", str(channel_public_id)),
        ("recipient-public-id", str(recipient_public_id)),
        ("message-fingerprint", message_fingerprint),
        ("receipt-requested", "true" if receipt_requested else "false"),
    )
    canonical = b"".join(
        _length_prefixed(label) + _length_prefixed(value)
        for label, value in fields
    )
    return RequestFingerprint(hashlib.sha256(canonical).hexdigest())


class DjangoAttemptRepository:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._clock = clock

    def accept(
        self,
        command: AcceptedDeliveryCommand,
    ) -> AttemptAcceptResult:
        if not isinstance(command, AcceptedDeliveryCommand):
            raise ValueError("invalid accepted delivery command")
        expected_fingerprint = build_request_fingerprint(
            owner=command.owner,
            owner_identity=command.owner_identity,
            channel_public_id=command.target.channel_public_id,
            recipient_public_id=command.target.recipient_public_id,
            message_fingerprint=command.message.fingerprint,
            receipt_requested=command.receipt_commitment is not None,
        )
        if command.request_fingerprint != expected_fingerprint:
            raise ValueError("request fingerprint does not match command")

        existing = DeliveryAttempt.objects.filter(
            operation_id=command.operation_id
        ).first()
        if existing is not None:
            return self._classify_operation(existing, command)

        accepted_at = _aware_datetime(self._clock())
        commitment = command.receipt_commitment
        try:
            with transaction.atomic():
                attempt = DeliveryAttempt.objects.create(
                    operation_id=command.operation_id,
                    subject=command.message.subject,
                    body=command.message.body,
                    formatted_text=command.message.formatted_text,
                    content_fingerprint=command.message.fingerprint,
                    active_content_fingerprint=None,
                    request_fingerprint=command.request_fingerprint.digest,
                    active_request_fingerprint=(
                        command.request_fingerprint.digest
                    ),
                    target_mode=DeliveryAttempt.TargetMode.LINKED_RECIPIENT,
                    owner_principal_slot=command.owner.slot,
                    owner_identity_public_id=command.owner_identity.public_id,
                    channel_public_id=command.target.channel_public_id,
                    channel_label_snapshot=command.target.channel_label,
                    recipient_public_id=command.target.recipient_public_id,
                    channel_active_snapshot=command.target.channel_active,
                    recipient_enabled_snapshot=(
                        command.target.recipient_enabled
                    ),
                    friendship_state_snapshot=(
                        command.target.friendship_state
                    ),
                    accepted_at=accepted_at,
                    processing_expires_at=(
                        accepted_at + PROCESSING_TIMEOUT
                    ),
                    receipt_requested=commitment is not None,
                    receipt_expires_at=(
                        commitment.expires_at
                        if commitment is not None
                        else None
                    ),
                    receipt_token_digest=(
                        commitment.digest
                        if commitment is not None
                        else None
                    ),
                )
            return AttemptAccepted(
                attempt_id=attempt.pk,
                snapshot=self._snapshot(attempt, now=accepted_at),
            )
        except IntegrityError:
            # create用savepointを抜けてから再読込し、壊れたtransactionを
            # callerへ持ち出さない。
            operation_attempt = DeliveryAttempt.objects.filter(
                operation_id=command.operation_id
            ).first()
            if operation_attempt is not None:
                return self._classify_operation(
                    operation_attempt,
                    command,
                )
            canonical_attempt = DeliveryAttempt.objects.filter(
                active_request_fingerprint=(
                    command.request_fingerprint.digest
                )
            ).first()
            if canonical_attempt is not None:
                return ExistingAttempt(
                    self._snapshot(
                        canonical_attempt,
                        now=_aware_datetime(self._clock()),
                    )
                )
            raise

    def finalize(
        self,
        attempt_id: int,
        result: LinePushResult,
        completed_at: datetime,
    ) -> DeliverySnapshot:
        if type(attempt_id) is not int or attempt_id <= 0:
            raise ValueError("invalid attempt ID")
        if not isinstance(
            result,
            (LinePushAccepted, LinePushRejected, LinePushUnknown),
        ):
            raise ValueError("invalid LINE push result")
        completed_at = _aware_datetime(completed_at)

        terminal_values: dict[str, object] = {
            "active_content_fingerprint": None,
            "active_request_fingerprint": None,
            "completed_at": completed_at,
        }
        if isinstance(result, LinePushAccepted):
            terminal_values.update(
                status=DeliveryAttempt.Status.SUCCEEDED,
                sent_at=completed_at,
                line_request_id=result.request_id,
                line_accepted_request_id=result.accepted_request_id,
            )
        else:
            terminal_values.update(
                status=(
                    DeliveryAttempt.Status.FAILED
                    if isinstance(result, LinePushRejected)
                    else DeliveryAttempt.Status.UNKNOWN
                ),
                failure_type=result.failure_type,
                failed_at=completed_at,
            )

        # Model instanceのread-modify-writeを避け、processingだけを終端化する。
        DeliveryAttempt.objects.filter(
            pk=attempt_id,
            status=DeliveryAttempt.Status.PROCESSING,
        ).update(**terminal_values)
        attempt = DeliveryAttempt.objects.get(pk=attempt_id)
        return self._snapshot(
            attempt,
            now=_aware_datetime(self._clock()),
        )

    def get_for_owner(
        self,
        owner_principal_slot: int,
        operation_id: UUID,
    ) -> DeliverySnapshot | None:
        if (
            type(owner_principal_slot) is not int
            or owner_principal_slot <= 0
        ):
            raise ValueError("invalid owner principal slot")
        if not isinstance(operation_id, UUID):
            raise ValueError("invalid operation ID")

        attempt = DeliveryAttempt.objects.filter(
            owner_principal_slot=owner_principal_slot,
            operation_id=operation_id,
        ).first()
        if attempt is None:
            return None

        now = _aware_datetime(self._clock())
        if (
            attempt.status == DeliveryAttempt.Status.PROCESSING
            and attempt.processing_expires_at <= now
        ):
            # owner scopeを満たした行だけを期限切れへCASし、競合時は
            # finalize側が先に保存した終端結果をそのまま再読込する。
            DeliveryAttempt.objects.filter(
                pk=attempt.pk,
                owner_principal_slot=owner_principal_slot,
                operation_id=operation_id,
                status=DeliveryAttempt.Status.PROCESSING,
                processing_expires_at__lte=now,
            ).update(
                status=DeliveryAttempt.Status.UNKNOWN,
                failure_type=(
                    DeliveryAttempt.FailureType.PROCESSING_EXPIRED
                ),
                active_content_fingerprint=None,
                active_request_fingerprint=None,
                failed_at=now,
                completed_at=now,
            )
            attempt = DeliveryAttempt.objects.get(pk=attempt.pk)
        return self._snapshot(attempt, now=now)

    def _classify_operation(
        self,
        attempt: DeliveryAttempt,
        command: AcceptedDeliveryCommand,
    ) -> AttemptAcceptResult:
        if attempt.request_fingerprint != command.request_fingerprint.digest:
            return AttemptConflict()
        return ExistingAttempt(
            self._snapshot(
                attempt,
                now=_aware_datetime(self._clock()),
            )
        )

    @staticmethod
    def _snapshot(
        attempt: DeliveryAttempt,
        *,
        now: datetime,
    ) -> DeliverySnapshot:
        if attempt.owner_principal_slot is None:
            raise ValueError("delivery attempt has no owner principal")
        owner = OwnerPrincipal(attempt.owner_principal_slot)
        owner_identity = (
            OwnerIdentitySnapshot(attempt.owner_identity_public_id)
            if attempt.owner_identity_public_id is not None
            else None
        )
        if attempt.target_mode == DeliveryAttempt.TargetMode.LINKED_RECIPIENT:
            if (
                attempt.channel_public_id is None
                or attempt.recipient_public_id is None
                or attempt.channel_label_snapshot is None
                or attempt.channel_active_snapshot is None
                or attempt.recipient_enabled_snapshot is None
                or attempt.friendship_state_snapshot is None
            ):
                raise ValueError("linked delivery attempt is incomplete")
            target = LinkedTargetSnapshot(
                channel_public_id=attempt.channel_public_id,
                channel_label=attempt.channel_label_snapshot,
                recipient_public_id=attempt.recipient_public_id,
                channel_active=attempt.channel_active_snapshot,
                recipient_enabled=attempt.recipient_enabled_snapshot,
                friendship_state=attempt.friendship_state_snapshot,
            )
        elif attempt.target_mode == DeliveryAttempt.TargetMode.FIXED_USER:
            target = FixedTargetSnapshot()
        else:
            raise ValueError("delivery attempt has invalid target mode")

        receipt_status = "not_requested"
        if attempt.receipt_requested:
            if attempt.receipt_confirmed_at is not None:
                receipt_status = "confirmed"
            elif (
                attempt.receipt_expires_at is not None
                and attempt.receipt_expires_at <= now
            ):
                receipt_status = "expired"
            else:
                receipt_status = "pending"

        return DeliverySnapshot(
            operation_id=attempt.operation_id,
            owner=owner,
            owner_identity=owner_identity,
            target=target,
            message=MessageSnapshot(
                subject=attempt.subject,
                body=attempt.body,
                formatted_text=attempt.formatted_text,
                fingerprint=attempt.content_fingerprint,
            ),
            status=attempt.status,
            accepted_at=attempt.accepted_at,
            completed_at=attempt.completed_at,
            line_request_id=attempt.line_request_id,
            line_accepted_request_id=attempt.line_accepted_request_id,
            failure=attempt.failure_type,
            receipt_status=receipt_status,
            receipt_expires_at=attempt.receipt_expires_at,
            receipt_confirmed_at=attempt.receipt_confirmed_at,
            receipt_webhook_event_id=attempt.receipt_webhook_event_id,
        )


def _length_prefixed(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded


def _aware_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock must return an aware datetime")
    return value
