from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import SimpleTestCase, TestCase

from delivery.receipt import ReceiptCapabilityFactory
from delivery.repositories import (
    DjangoAttemptRepository,
    build_request_fingerprint,
)
from delivery.services import DeliveryService
from delivery.types import (
    AcceptedDeliveryCommand,
    AttemptAccepted,
    AttemptConflict,
    ConfirmationSnapshot,
    DeliverySnapshot,
    ExistingAttempt,
    LinkedPushExecuted,
    LinkedPushPrevented,
    LinkedPushStored,
    LinkedTargetSnapshot,
    LinePushAccepted,
    LinePushRejected,
    LinePushUnknown,
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
from linechannels.repositories import CredentialRepository
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialAvailable,
    CredentialUnavailable,
)


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


class FakeCredentialRepository:
    def __init__(self, result, *, callback=None):
        self.result = result
        self.callback = callback
        self.calls = []

    def get_access_token(self, channel_public_id):
        self.calls.append(channel_public_id)
        if self.callback is not None:
            self.callback()
        return self.result

    def get_channel_secret(self, channel_public_id):
        raise AssertionError("channel secret must not be requested")


class FakePushGateway:
    def __init__(self, result):
        self.result = result
        self.commands = []
        self.atomic_states = []

    def push(self, command):
        self.commands.append(command)
        self.atomic_states.append(connection.in_atomic_block)
        return self.result


class FakeFinalizingAttemptRepository(FakeAttemptRepository):
    def __init__(self, result, *, final_snapshot=None):
        super().__init__(result)
        self.final_snapshot = final_snapshot
        self.finalizations = []

    def finalize(self, attempt_id, result, completed_at):
        self.finalizations.append((attempt_id, result, completed_at))
        if self.final_snapshot is not None:
            return self.final_snapshot
        return replace(
            self.result.snapshot,
            status="failed",
            completed_at=completed_at,
            failure=result.failure_type,
        )

    def get_for_owner(self, owner_principal_slot, operation_id):
        self.status_lookups = getattr(self, "status_lookups", [])
        self.status_lookups.append((owner_principal_slot, operation_id))
        return self.final_snapshot


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


class LinkedDeliveryPushTests(LinkedDeliveryAcceptTests):
    def setUp(self):
        super().setUp()
        self.snapshot = self._snapshot(self.operation_id)
        self.accepted = self._accepted()

    def test_selected_credential_and_revalidated_target_are_pushed_once(self):
        token = AccessToken("selected-token-canary")
        credentials = FakeCredentialRepository(CredentialAvailable(token))
        directory = FakeDirectory(self.live_target)
        gateway_result = LinePushAccepted("request-id", None)
        gateway = FakePushGateway(gateway_result)
        repository = FakeFinalizingAttemptRepository(
            AttemptAccepted(9, self.snapshot)
        )
        service = self._push_service(
            directory=directory,
            credentials=credentials,
            gateway=gateway,
            repository=repository,
        )

        result = service.push_accepted(self.accepted)

        self.assertIsInstance(credentials, CredentialRepository)
        self.assertEqual(credentials.calls, [self.channel_id])
        self.assertEqual(
            directory.calls,
            [(self.owner_identity.public_id, self.channel_id, self.recipient_id)],
        )
        self.assertIsInstance(result, LinkedPushExecuted)
        self.assertEqual(result.attempt_id, 9)
        self.assertIs(result.result, gateway_result)
        self.assertEqual(len(gateway.commands), 1)
        pushed = gateway.commands[0]
        self.assertEqual(pushed.operation_id, self.operation_id)
        self.assertIs(pushed.access_token, token)
        self.assertIs(pushed.subject, self.live_target.subject)
        self.assertEqual(pushed.text, self.message.formatted_text)
        self.assertIs(pushed.receipt_capability, self.candidate.capability)
        self.assertEqual(gateway.atomic_states, [False])
        self.assertEqual(repository.finalizations, [])
        self.assertNotIn("selected-token-canary", repr(result))
        self.assertNotIn("U-secret-subject", repr(result))
        self.assertNotIn("raw-capability-secret", repr(result))

    def test_credential_unavailable_finalizes_without_fallback_or_push(self):
        credentials = FakeCredentialRepository(
            CredentialUnavailable("credential_unreadable")
        )
        directory = FakeDirectory(self.live_target)
        gateway = FakePushGateway(LinePushAccepted(None, None))
        repository = FakeFinalizingAttemptRepository(
            AttemptAccepted(9, self.snapshot)
        )

        result = self._push_service(
            directory=directory,
            credentials=credentials,
            gateway=gateway,
            repository=repository,
        ).push_accepted(self.accepted)

        self.assertIsInstance(result, LinkedPushPrevented)
        self.assertEqual(result.failure_type, "configuration")
        self.assertEqual(credentials.calls, [self.channel_id])
        self.assertEqual(directory.calls, [])
        self.assertEqual(gateway.commands, [])
        self.assertEqual(len(repository.finalizations), 1)
        attempt_id, failure, completed_at = repository.finalizations[0]
        self.assertEqual(attempt_id, 9)
        self.assertEqual(failure.failure_type, "configuration")
        self.assertEqual(completed_at, NOW)

    def test_target_changed_after_credential_finalizes_without_push(self):
        changed_target = LiveDeliveryTarget(
            owner_identity=self.owner_identity,
            provider_id="line",
            snapshot=self.target_snapshot,
            revision=TargetRevision("c" * 64),
            subject=LineSubject("U-other-secret-subject"),
            delivery_available=True,
        )
        directory = FakeDirectory(changed_target)
        credentials = FakeCredentialRepository(
            CredentialAvailable(AccessToken("selected-token-canary"))
        )
        gateway = FakePushGateway(LinePushAccepted(None, None))
        repository = FakeFinalizingAttemptRepository(
            AttemptAccepted(9, self.snapshot)
        )

        result = self._push_service(
            directory=directory,
            credentials=credentials,
            gateway=gateway,
            repository=repository,
        ).push_accepted(self.accepted)

        self.assertIsInstance(result, LinkedPushPrevented)
        self.assertEqual(result.failure_type, "target_changed")
        self.assertEqual(len(directory.calls), 1)
        self.assertEqual(gateway.commands, [])
        self.assertEqual(len(repository.finalizations), 1)
        self.assertEqual(
            repository.finalizations[0][1].failure_type,
            "target_changed",
        )
        self.assertNotIn("selected-token-canary", repr(result))
        self.assertNotIn("U-other-secret-subject", repr(result))

    def test_target_is_resolved_only_after_selected_credential_is_fetched(self):
        events = []

        class OrderedDirectory(FakeDirectory):
            def resolve(inner_self, *args):
                events.append("resolve")
                return super().resolve(*args)

        credentials = FakeCredentialRepository(
            CredentialAvailable(AccessToken("selected-token")),
            callback=lambda: events.append("credential"),
        )
        gateway = FakePushGateway(LinePushAccepted(None, None))

        self._push_service(
            directory=OrderedDirectory(self.live_target),
            credentials=credentials,
            gateway=gateway,
        ).push_accepted(self.accepted)

        self.assertEqual(events, ["credential", "resolve"])
        self.assertEqual(len(gateway.commands), 1)

    def test_invalid_credential_result_is_a_safe_programming_error(self):
        invalid_results = (
            CredentialAvailable(ChannelSecret("wrong-secret-type")),
            object(),
        )
        for invalid_result in invalid_results:
            with self.subTest(result=type(invalid_result).__name__):
                gateway = FakePushGateway(LinePushAccepted(None, None))
                repository = FakeFinalizingAttemptRepository(
                    AttemptAccepted(9, self.snapshot)
                )

                with self.assertRaisesMessage(
                    ValueError,
                    "invalid access token credential result",
                ):
                    self._push_service(
                        directory=FakeDirectory(self.live_target),
                        credentials=FakeCredentialRepository(invalid_result),
                        gateway=gateway,
                        repository=repository,
                    ).push_accepted(self.accepted)

                self.assertEqual(gateway.commands, [])
                self.assertEqual(repository.finalizations, [])

    def test_pre_push_finalize_race_returns_actual_stored_terminal_state(self):
        stored_snapshots = (
            (
                "configuration",
                replace(
                    self.snapshot,
                    status="succeeded",
                    completed_at=NOW,
                    line_request_id="winner-request",
                    line_accepted_request_id=None,
                    failure=None,
                ),
            ),
            (
                "target_changed",
                replace(
                    self.snapshot,
                    status="unknown",
                    completed_at=NOW,
                    failure="timeout_unknown",
                ),
            ),
            (
                "configuration",
                replace(
                    self.snapshot,
                    status="failed",
                    completed_at=NOW,
                    failure="authentication",
                ),
            ),
        )
        for requested_failure, stored_snapshot in stored_snapshots:
            with self.subTest(requested_failure=requested_failure):
                repository = FakeFinalizingAttemptRepository(
                    AttemptAccepted(9, self.snapshot),
                    final_snapshot=stored_snapshot,
                )
                gateway = FakePushGateway(LinePushAccepted(None, None))
                if requested_failure == "configuration":
                    credentials = FakeCredentialRepository(
                        CredentialUnavailable("credential_unreadable")
                    )
                    directory = FakeDirectory(self.live_target)
                else:
                    credentials = FakeCredentialRepository(
                        CredentialAvailable(AccessToken("selected-token"))
                    )
                    directory = FakeDirectory(TargetUnavailable())

                result = self._push_service(
                    directory=directory,
                    credentials=credentials,
                    gateway=gateway,
                    repository=repository,
                ).push_accepted(self.accepted)

                self.assertIsInstance(result, LinkedPushStored)
                self.assertIs(result.snapshot, stored_snapshot)
                self.assertEqual(
                    result.snapshot.status,
                    stored_snapshot.status,
                )
                self.assertEqual(
                    result.snapshot.failure,
                    stored_snapshot.failure,
                )
                self.assertEqual(gateway.commands, [])
                self.assertEqual(
                    repository.finalizations[0][1].failure_type,
                    requested_failure,
                )

    def test_prevented_result_rejects_snapshot_failure_mismatch(self):
        with self.assertRaisesMessage(
            ValueError,
            "pre-push failure does not match stored snapshot",
        ):
            LinkedPushPrevented(
                snapshot=replace(
                    self.snapshot,
                    status="failed",
                    completed_at=NOW,
                    failure="target_changed",
                ),
                failure_type="configuration",
            )

    def test_each_revalidation_axis_blocks_gateway(self):
        other_channel_snapshot = LinkedTargetSnapshot(
            channel_public_id=uuid4(),
            channel_label="別通知用",
            recipient_public_id=self.recipient_id,
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        other_recipient_snapshot = LinkedTargetSnapshot(
            channel_public_id=self.channel_id,
            channel_label="通知用",
            recipient_public_id=uuid4(),
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        unavailable_snapshot = replace(
            self.target_snapshot,
            recipient_enabled=False,
        )
        invalid_targets = (
            TargetUnavailable(),
            replace(
                self.live_target,
                owner_identity=OwnerIdentitySnapshot(uuid4()),
            ),
            replace(self.live_target, provider_id="other-provider"),
            replace(self.live_target, snapshot=other_channel_snapshot),
            replace(self.live_target, snapshot=other_recipient_snapshot),
            replace(
                self.live_target,
                revision=TargetRevision("c" * 64),
            ),
            replace(
                self.live_target,
                snapshot=unavailable_snapshot,
                delivery_available=False,
            ),
        )
        for current_target in invalid_targets:
            with self.subTest(target=repr(current_target)):
                gateway = FakePushGateway(LinePushAccepted(None, None))
                repository = FakeFinalizingAttemptRepository(
                    AttemptAccepted(9, self.snapshot)
                )

                result = self._push_service(
                    directory=FakeDirectory(current_target),
                    credentials=FakeCredentialRepository(
                        CredentialAvailable(AccessToken("selected-token"))
                    ),
                    gateway=gateway,
                    repository=repository,
                ).push_accepted(self.accepted)

                self.assertIsInstance(result, LinkedPushPrevented)
                self.assertEqual(result.failure_type, "target_changed")
                self.assertEqual(gateway.commands, [])

    def _accepted(self):
        accepted_result = AttemptAccepted(9, self.snapshot)
        return DeliveryService(
            target_directory=FakeDirectory(self.live_target),
            attempt_repository=FakeAttemptRepository(accepted_result),
            receipt_capability_factory=FakeReceiptFactory(self.candidate),
        ).accept_confirmed(self._command())

    def _push_service(
        self,
        *,
        directory,
        credentials,
        gateway,
        repository=None,
    ):
        return DeliveryService(
            clock=lambda: NOW,
            target_directory=directory,
            attempt_repository=(
                repository
                or FakeFinalizingAttemptRepository(
                    AttemptAccepted(9, self.snapshot)
                )
            ),
            credential_repository=credentials,
            channel_push_gateway=gateway,
        )


class LinkedDeliveryFinalizationTests(LinkedDeliveryAcceptTests):
    def setUp(self):
        super().setUp()
        self.processing = self._snapshot(self.operation_id)

    def test_gateway_results_converge_to_the_repository_snapshot(self):
        cases = (
            (
                LinePushAccepted("request-id", "accepted-request-id"),
                replace(
                    self.processing,
                    status="succeeded",
                    completed_at=NOW,
                    line_request_id="request-id",
                    line_accepted_request_id="accepted-request-id",
                ),
            ),
            (
                LinePushRejected("authentication"),
                replace(
                    self.processing,
                    status="failed",
                    completed_at=NOW,
                    failure="authentication",
                ),
            ),
            (
                LinePushUnknown("timeout_unknown"),
                replace(
                    self.processing,
                    status="unknown",
                    completed_at=NOW,
                    failure="timeout_unknown",
                ),
            ),
        )
        for gateway_result, stored in cases:
            with self.subTest(status=gateway_result.status):
                repository = FakeFinalizingAttemptRepository(
                    AttemptAccepted(9, self.processing),
                    final_snapshot=stored,
                )
                service = DeliveryService(
                    clock=lambda: NOW,
                    attempt_repository=repository,
                )

                result = service.finalize_linked_push(
                    LinkedPushExecuted(9, gateway_result)
                )

                self.assertIsInstance(result, LinkedPushStored)
                self.assertIs(result.snapshot, stored)
                self.assertEqual(
                    repository.finalizations,
                    [(9, gateway_result, NOW)],
                )
                self.assertEqual(result.snapshot.receipt_status, "pending")
                self.assertEqual(
                    result.snapshot.receipt_expires_at,
                    self.expiry,
                )

    def test_finalize_race_returns_first_stored_terminal_result(self):
        first_terminal = replace(
            self.processing,
            status="unknown",
            completed_at=NOW,
            failure="service_unknown",
        )
        repository = FakeFinalizingAttemptRepository(
            AttemptAccepted(9, self.processing),
            final_snapshot=first_terminal,
        )
        service = DeliveryService(
            clock=lambda: NOW,
            attempt_repository=repository,
        )

        result = service.finalize_linked_push(
            LinkedPushExecuted(
                9,
                LinePushAccepted("late-request", None),
            )
        )

        self.assertIs(result.snapshot, first_terminal)
        self.assertEqual(result.snapshot.status, "unknown")
        self.assertEqual(result.snapshot.failure, "service_unknown")
        self.assertIsNone(result.snapshot.line_request_id)

    def test_repeated_unknown_completion_only_reuses_repository_result(self):
        unknown = replace(
            self.processing,
            status="unknown",
            completed_at=NOW,
            failure="response_unknown",
        )
        repository = FakeFinalizingAttemptRepository(
            AttemptAccepted(9, self.processing),
            final_snapshot=unknown,
        )
        forbidden = patch(
            "delivery.services.LINEGateway",
            side_effect=AssertionError("must not construct a gateway"),
        )
        with forbidden as gateway_constructor:
            service = DeliveryService(
                clock=lambda: NOW,
                attempt_repository=repository,
            )
            for _ in range(2):
                result = service.finalize_linked_push(
                    LinkedPushExecuted(
                        9,
                        LinePushUnknown("response_unknown"),
                    )
                )
                self.assertIs(result.snapshot, unknown)

        self.assertEqual(len(repository.finalizations), 2)
        gateway_constructor.assert_not_called()

    def test_already_stored_results_cannot_be_finalized_as_gateway_work(self):
        failed = replace(
            self.processing,
            status="failed",
            completed_at=NOW,
            failure="configuration",
        )
        repository = FakeFinalizingAttemptRepository(
            AttemptAccepted(9, self.processing),
            final_snapshot=failed,
        )
        service = DeliveryService(attempt_repository=repository)

        for result in (
            ExistingAttempt(failed),
            LinkedPushStored(failed),
            LinkedPushPrevented(failed, "configuration"),
        ):
            with self.subTest(result=type(result).__name__):
                with self.assertRaisesMessage(
                    ValueError,
                    "invalid linked push execution result",
                ):
                    service.finalize_linked_push(result)

        self.assertEqual(repository.finalizations, [])


class LinkedDeliveryOwnerStatusTests(LinkedDeliveryAcceptTests):
    def test_status_delegates_owner_scope_and_expiry_to_repository_only(self):
        processing = self._snapshot(self.operation_id)
        expired = replace(
            processing,
            status="unknown",
            completed_at=NOW,
            failure="processing_expired",
        )
        repository = FakeFinalizingAttemptRepository(
            AttemptAccepted(9, processing),
            final_snapshot=expired,
        )
        service = DeliveryService(
            attempt_repository=repository,
            target_directory=object(),
            receipt_capability_factory=object(),
            credential_repository=object(),
            channel_push_gateway=object(),
        )

        first = service.check_linked_status(
            self.owner.slot,
            self.operation_id,
        )
        second = service.check_linked_status(
            self.owner.slot,
            self.operation_id,
        )

        self.assertIs(first, expired)
        self.assertIs(second, expired)
        self.assertEqual(
            repository.status_lookups,
            [
                (self.owner.slot, self.operation_id),
                (self.owner.slot, self.operation_id),
            ],
        )
        self.assertEqual(repository.finalizations, [])

    def test_other_owner_and_missing_operation_are_hidden(self):
        repository = FakeFinalizingAttemptRepository(
            AttemptAccepted(9, self._snapshot(self.operation_id)),
            final_snapshot=None,
        )
        service = DeliveryService(attempt_repository=repository)

        self.assertIsNone(
            service.check_linked_status(999, self.operation_id)
        )
        self.assertEqual(
            repository.status_lookups,
            [(999, self.operation_id)],
        )


class LinkedDeliveryStoredResultIntegrationTests(TestCase):
    def setUp(self):
        self.now = NOW
        self.owner = OwnerPrincipal(42)
        self.owner_identity = OwnerIdentitySnapshot(uuid4())
        self.target = LinkedTargetSnapshot(
            channel_public_id=uuid4(),
            channel_label="通知用",
            recipient_public_id=uuid4(),
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        self.message = MessageSnapshot(
            subject="件名",
            body="本文",
            formatted_text="件名\n\n本文",
            fingerprint=DIGEST_B,
        )
        self.repository = DjangoAttemptRepository(clock=lambda: self.now)
        self.service = DeliveryService(
            clock=lambda: self.now,
            attempt_repository=self.repository,
        )

    def test_finalize_and_owner_status_preserve_first_terminal_and_receipt(self):
        accepted = self.repository.accept(self._command(receipt=True))
        self.now = NOW + timedelta(seconds=1)

        first = self.service.finalize_linked_push(
            LinkedPushExecuted(
                accepted.attempt_id,
                LinePushAccepted("request-id", "accepted-request-id"),
            )
        )
        self.now = NOW + timedelta(seconds=2)
        later = self.service.finalize_linked_push(
            LinkedPushExecuted(
                accepted.attempt_id,
                LinePushUnknown("timeout_unknown"),
            )
        )
        visible = self.service.check_linked_status(
            self.owner.slot,
            accepted.snapshot.operation_id,
        )

        self.assertEqual(first.snapshot.status, "succeeded")
        self.assertEqual(later.snapshot.status, "succeeded")
        self.assertEqual(visible.status, "succeeded")
        self.assertEqual(visible.completed_at, NOW + timedelta(seconds=1))
        self.assertEqual(visible.line_request_id, "request-id")
        self.assertEqual(
            visible.line_accepted_request_id,
            "accepted-request-id",
        )
        self.assertEqual(visible.receipt_status, "pending")
        self.assertEqual(
            visible.receipt_expires_at,
            NOW + timedelta(hours=1),
        )
        self.assertIsNone(
            self.service.check_linked_status(
                self.owner.slot + 1,
                accepted.snapshot.operation_id,
            )
        )

    def test_status_expires_processing_at_exact_deadline_without_push(self):
        accepted = self.repository.accept(self._command(receipt=False))
        before = self.service.check_linked_status(
            self.owner.slot,
            accepted.snapshot.operation_id,
        )
        self.now = NOW + timedelta(seconds=30)

        expired = self.service.check_linked_status(
            self.owner.slot,
            accepted.snapshot.operation_id,
        )
        repeated = self.service.check_linked_status(
            self.owner.slot,
            accepted.snapshot.operation_id,
        )

        self.assertEqual(before.status, "processing")
        self.assertEqual(expired.status, "unknown")
        self.assertEqual(expired.failure, "processing_expired")
        self.assertEqual(expired.completed_at, self.now)
        self.assertEqual(repeated, expired)
        self.assertEqual(repeated.receipt_status, "not_requested")

    def _command(self, *, receipt):
        operation_id = uuid4()
        commitment = (
            ReceiptCommitment(DIGEST_A, NOW + timedelta(hours=1))
            if receipt
            else None
        )
        return AcceptedDeliveryCommand(
            operation_id=operation_id,
            owner=self.owner,
            owner_identity=self.owner_identity,
            target=self.target,
            message=self.message,
            request_fingerprint=build_request_fingerprint(
                owner=self.owner,
                owner_identity=self.owner_identity,
                channel_public_id=self.target.channel_public_id,
                recipient_public_id=self.target.recipient_public_id,
                message_fingerprint=self.message.fingerprint,
                receipt_requested=receipt,
            ),
            receipt_commitment=commitment,
        )
