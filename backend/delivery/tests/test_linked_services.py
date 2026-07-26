from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase

from delivery.receipt import ReceiptCapabilityFactory
from delivery.services import DeliveryService
from delivery.types import (
    AttemptAccepted,
    AttemptConflict,
    ConfirmationSnapshot,
    DeliverySnapshot,
    ExistingAttempt,
    LinkedTargetSnapshot,
    LiveDeliveryTarget,
    MessageSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
    ReceiptCapability,
    ReceiptCapabilityCandidate,
    ReceiptCommitment,
    SubmitLinkedDelivery,
    TargetRevision,
    TargetUnavailable,
)
from lineaccounts.types import LineSubject


NOW = datetime(2026, 7, 26, 1, 2, 3, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class FakeDirectory:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def resolve(self, owner_identity_id, channel_id, recipient_id):
        self.calls.append((owner_identity_id, channel_id, recipient_id))
        return self.result


class FakeAttemptRepository:
    def __init__(self, result):
        self.result = result
        self.commands = []

    def accept(self, command):
        self.commands.append(command)
        return self.result


class FakeReceiptFactory:
    def __init__(self, candidate):
        self.candidate = candidate
        self.calls = []

    def create(self, expires_at):
        self.calls.append(expires_at)
        return self.candidate


class LinkedDeliveryAcceptTests(SimpleTestCase):
    def setUp(self):
        self.owner = OwnerPrincipal(42)
        self.owner_identity = OwnerIdentitySnapshot(uuid4())
        self.channel_id = uuid4()
        self.recipient_id = uuid4()
        self.revision = TargetRevision(DIGEST_A)
        self.target_snapshot = LinkedTargetSnapshot(
            channel_public_id=self.channel_id,
            channel_label="通知用",
            recipient_public_id=self.recipient_id,
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        self.live_target = LiveDeliveryTarget(
            owner_identity=self.owner_identity,
            provider_id="line",
            snapshot=self.target_snapshot,
            revision=self.revision,
            subject=LineSubject("U-secret-subject"),
            delivery_available=True,
        )
        self.message = MessageSnapshot(
            subject="件名",
            body="本文",
            formatted_text="件名\n\n本文",
            fingerprint=DIGEST_B,
        )
        self.expiry = datetime(2026, 7, 27, 1, 2, 3, tzinfo=UTC)
        self.confirmation = ConfirmationSnapshot(
            owner=self.owner,
            owner_identity=self.owner_identity,
            channel_public_id=self.channel_id,
            recipient_public_id=self.recipient_id,
            target_revision=self.revision,
            message_fingerprint=self.message.fingerprint,
            receipt_requested=True,
            receipt_expires_at=self.expiry,
        )
        self.candidate = ReceiptCapabilityCandidate(
            capability=ReceiptCapability("raw-capability-secret"),
            commitment=ReceiptCommitment(DIGEST_A, self.expiry),
        )
        self.operation_id = uuid4()

    def test_new_attempt_returns_internal_push_preparation_only_after_accept(self):
        snapshot = self._snapshot(self.operation_id)
        repository = FakeAttemptRepository(AttemptAccepted(9, snapshot))
        directory = FakeDirectory(self.live_target)
        factory = FakeReceiptFactory(self.candidate)
        service = DeliveryService(
            target_directory=directory,
            attempt_repository=repository,
            receipt_capability_factory=factory,
        )

        result = service.accept_confirmed(self._command())

        self.assertEqual(result.attempt_id, 9)
        self.assertEqual(result.snapshot, snapshot)
        self.assertIs(result.push_preparation.target, self.live_target)
        self.assertIs(
            result.push_preparation.receipt_capability,
            self.candidate.capability,
        )
        self.assertEqual(factory.calls, [self.expiry])
        accepted = repository.commands[0]
        self.assertEqual(accepted.operation_id, self.operation_id)
        self.assertEqual(accepted.owner, self.owner)
        self.assertEqual(accepted.owner_identity, self.owner_identity)
        self.assertEqual(accepted.target, self.target_snapshot)
        self.assertEqual(accepted.message, self.message)
        self.assertEqual(accepted.receipt_commitment, self.candidate.commitment)
        self.assertNotIn("raw-capability-secret", repr(result))
        self.assertNotIn("U-secret-subject", repr(result))

    def test_existing_operation_returns_only_stored_snapshot(self):
        existing = ExistingAttempt(self._snapshot(self.operation_id))
        repository = FakeAttemptRepository(existing)
        service = self._service(repository)

        result = service.accept_confirmed(self._command())

        self.assertIs(result, existing)
        self.assertFalse(hasattr(result, "push_preparation"))
        self.assertFalse(hasattr(repository.commands[0], "receipt_capability"))
        self.assertNotIn("raw-capability-secret", repr(result))

    def test_active_request_conflict_returns_canonical_existing_snapshot(self):
        canonical_id = uuid4()
        existing = ExistingAttempt(self._snapshot(canonical_id))
        repository = FakeAttemptRepository(existing)

        result = self._service(repository).accept_confirmed(self._command())

        self.assertIs(result, existing)
        self.assertEqual(result.snapshot.operation_id, canonical_id)

    def test_operation_conflict_does_not_expose_candidate(self):
        conflict = AttemptConflict()
        repository = FakeAttemptRepository(conflict)

        result = self._service(repository).accept_confirmed(self._command())

        self.assertIs(result, conflict)
        self.assertFalse(hasattr(result, "push_preparation"))
        self.assertNotIn("raw-capability-secret", repr(result))

    def test_stale_revision_is_rejected_before_factory_or_accept(self):
        stale_target = LiveDeliveryTarget(
            owner_identity=self.owner_identity,
            provider_id="line",
            snapshot=self.target_snapshot,
            revision=TargetRevision("c" * 64),
            subject=LineSubject("U-secret-subject"),
            delivery_available=True,
        )
        repository = FakeAttemptRepository(AttemptConflict())
        factory = FakeReceiptFactory(self.candidate)
        service = DeliveryService(
            target_directory=FakeDirectory(stale_target),
            attempt_repository=repository,
            receipt_capability_factory=factory,
        )

        result = service.accept_confirmed(self._command())

        self.assertIsInstance(result, TargetUnavailable)
        self.assertEqual(factory.calls, [])
        self.assertEqual(repository.commands, [])

    def test_hidden_or_non_deliverable_target_is_rejected_before_accept(self):
        for unavailable in (
            TargetUnavailable(),
            self._non_deliverable_target(),
        ):
            with self.subTest(target=type(unavailable).__name__):
                repository = FakeAttemptRepository(AttemptConflict())
                service = DeliveryService(
                    target_directory=FakeDirectory(unavailable),
                    attempt_repository=repository,
                    receipt_capability_factory=FakeReceiptFactory(
                        self.candidate
                    ),
                )

                result = service.accept_confirmed(self._command())

                self.assertIsInstance(result, TargetUnavailable)
                self.assertEqual(repository.commands, [])

    def test_receipt_not_requested_skips_candidate_factory(self):
        confirmation = ConfirmationSnapshot(
            owner=self.owner,
            owner_identity=self.owner_identity,
            channel_public_id=self.channel_id,
            recipient_public_id=self.recipient_id,
            target_revision=self.revision,
            message_fingerprint=self.message.fingerprint,
            receipt_requested=False,
            receipt_expires_at=None,
        )
        factory = FakeReceiptFactory(self.candidate)
        snapshot = self._snapshot(self.operation_id, receipt_requested=False)
        repository = FakeAttemptRepository(AttemptAccepted(3, snapshot))
        service = DeliveryService(
            target_directory=FakeDirectory(self.live_target),
            attempt_repository=repository,
            receipt_capability_factory=factory,
        )

        result = service.accept_confirmed(
            SubmitLinkedDelivery(
                operation_id=self.operation_id,
                confirmation=confirmation,
                message=self.message,
            )
        )

        self.assertEqual(factory.calls, [])
        self.assertIsNone(repository.commands[0].receipt_commitment)
        self.assertIsNone(result.push_preparation.receipt_capability)

    @patch(
        "delivery.services.LINEGateway",
        side_effect=AssertionError("linked accept must not construct gateway"),
    )
    def test_accept_stage_has_no_gateway_dependency_or_call(self, _gateway):
        repository = FakeAttemptRepository(
            ExistingAttempt(self._snapshot(self.operation_id))
        )
        service = self._service(repository)

        result = service.accept_confirmed(self._command())

        self.assertIsInstance(result, ExistingAttempt)
        _gateway.assert_not_called()

    def _service(self, repository):
        return DeliveryService(
            target_directory=FakeDirectory(self.live_target),
            attempt_repository=repository,
            receipt_capability_factory=FakeReceiptFactory(self.candidate),
        )

    def _command(self):
        return SubmitLinkedDelivery(
            operation_id=self.operation_id,
            confirmation=self.confirmation,
            message=self.message,
        )

    def _snapshot(self, operation_id, *, receipt_requested=True):
        return DeliverySnapshot(
            operation_id=operation_id,
            owner=self.owner,
            owner_identity=self.owner_identity,
            target=self.target_snapshot,
            message=self.message,
            status="processing",
            accepted_at=NOW,
            completed_at=None,
            line_request_id=None,
            line_accepted_request_id=None,
            failure=None,
            receipt_status=(
                "pending" if receipt_requested else "not_requested"
            ),
            receipt_expires_at=(
                self.expiry if receipt_requested else None
            ),
            receipt_confirmed_at=None,
            receipt_webhook_event_id=None,
        )

    def _non_deliverable_target(self):
        snapshot = LinkedTargetSnapshot(
            channel_public_id=self.channel_id,
            channel_label="通知用",
            recipient_public_id=self.recipient_id,
            channel_active=True,
            recipient_enabled=False,
            friendship_state="friend",
        )
        return LiveDeliveryTarget(
            owner_identity=self.owner_identity,
            provider_id="line",
            snapshot=snapshot,
            revision=self.revision,
            subject=LineSubject("U-secret-subject"),
            delivery_available=False,
        )
