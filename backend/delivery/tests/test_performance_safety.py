from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, TypeVar
from uuid import UUID, uuid4

from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from delivery.models import DeliveryAttempt
from delivery.repositories import (
    DjangoAttemptRepository,
    build_request_fingerprint,
)
from delivery.services import DeliveryService
from delivery.types import (
    AcceptedDeliveryCommand,
    AcceptedLinkedAttempt,
    AttemptAccepted,
    ConfirmReceiptCommand,
    ConfirmationSnapshot,
    ExistingAttempt,
    LinkedPushExecuted,
    LinkedTargetSnapshot,
    LinePushAccepted,
    LinePushRejected,
    LinePushUnknown,
    LiveDeliveryTarget,
    MessageSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
    ReceiptCommitment,
    ReceiptRecorded,
    ReceiptUnchanged,
    SubmitLinkedDelivery,
    TargetRevision,
)
from lineaccounts.authentication import OWNER_SESSION_KEY
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import DeliveryRecipient, LineIdentity
from lineaccounts.repositories import DjangoAccountRepository
from lineaccounts.types import LineSubject
from linechannels.models import LineChannel
from linechannels.types import AccessToken, CredentialAvailable


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
THREAD_DEADLINE_SECONDS = 10
PROCESSING_DEADLINE_SECONDS = 30
_T = TypeVar("_T")


class LinkedDeliveryQueryBudgetTests(TestCase):
    def setUp(self) -> None:
        self.provider_id = "0012345678"
        account_repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = account_repository.lock_owner_account()
            identity = account_repository.upsert_identity(
                VerifiedLineIdentity(
                    self.provider_id,
                    LineSubject("Uquery-budget-subject"),
                    "負荷検証 recipient",
                )
            )
            owner = account_repository.bind_owner_identity(
                owner,
                identity.public_id,
            )
            self.owner_session = account_repository.create_owner_session(
                owner,
                timezone.now() + timedelta(hours=8),
            )
        self.identity = LineIdentity.objects.get(public_id=identity.public_id)
        self.channel, self.recipient = self._target("基準チャネル")
        self.attempt = self._terminal_attempt()

        client = APIClient(enforce_csrf_checks=True)
        session = client.session
        session[OWNER_SESSION_KEY] = str(self.owner_session.public_id)
        session.save()
        bootstrap = client.get("/api/account/session/")
        client.credentials(
            HTTP_ORIGIN="https://test.example.ngrok.app",
            HTTP_X_CSRFTOKEN=bootstrap.cookies["csrftoken"].value,
        )
        self.client = client

    # テストケース: fixtureを1件から13件へ増やしてchannel・recipient一覧、preview、statusを実行する。
    # 期待値: 各query数は件数増加前後で同じで、上限5・5・7・6 query以内に固定される。
    def test_list_preview_and_status_queries_are_bounded_without_n_plus_one(
        self,
    ) -> None:
        baseline = self._measure_endpoint_queries()
        for index in range(12):
            self._target(f"追加チャネル{index:02d}")
            self._terminal_attempt()

        scaled = self._measure_endpoint_queries()

        self.assertEqual(scaled, baseline)
        self.assertLessEqual(baseline["channels"], 5)
        self.assertLessEqual(baseline["recipients"], 5)
        self.assertLessEqual(baseline["preview"], 7)
        self.assertLessEqual(baseline["status"], 6)

    def _measure_endpoint_queries(self) -> dict[str, int]:
        operations = {
            "channels": lambda: self.client.get(
                "/api/deliveries/targets/channels/"
            ),
            "recipients": lambda: self.client.get(
                f"/api/deliveries/targets/channels/{self.channel.public_id}/"
                "recipients/"
            ),
            "preview": lambda: self.client.post(
                "/api/deliveries/preview/",
                {
                    "channelId": str(self.channel.public_id),
                    "recipientId": str(self.recipient.public_id),
                    "subject": "query budget",
                    "body": "fixture件数に依存しない",
                    "receiptRequested": False,
                },
                format="json",
            ),
            "status": lambda: self.client.post(
                f"/api/deliveries/{self.attempt.operation_id}/status/",
                {},
                format="json",
            ),
        }
        counts = {}
        for name, operation in operations.items():
            with CaptureQueriesContext(connection) as queries:
                response = operation()
            self.assertIn(response.status_code, (200, 202))
            counts[name] = len(queries)
        return counts

    def _target(
        self,
        label: str,
    ) -> tuple[LineChannel, DeliveryRecipient]:
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label=label,
            provider_id=self.provider_id,
            is_active=True,
        )
        recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=channel,
            enabled=True,
            friendship_state=DeliveryRecipient.FriendshipState.FRIEND,
        )
        return channel, recipient

    def _terminal_attempt(self) -> DeliveryAttempt:
        operation_id = uuid4()
        message_fingerprint = uuid4().hex + uuid4().hex
        accepted_at = timezone.now() - timedelta(seconds=2)
        return DeliveryAttempt.objects.create(
            operation_id=operation_id,
            owner_principal_slot=self.owner_session.owner_slot,
            owner_identity_public_id=self.identity.public_id,
            target_mode=DeliveryAttempt.TargetMode.LINKED_RECIPIENT,
            channel_public_id=self.channel.public_id,
            channel_label_snapshot=self.channel.label,
            recipient_public_id=self.recipient.public_id,
            channel_active_snapshot=True,
            recipient_enabled_snapshot=True,
            friendship_state_snapshot="friend",
            subject="query budget",
            body="fixture件数に依存しない",
            formatted_text="【query budget】\n\nfixture件数に依存しない",
            request_fingerprint=message_fingerprint,
            status=DeliveryAttempt.Status.SUCCEEDED,
            accepted_at=accepted_at,
            processing_expires_at=accepted_at
            + timedelta(seconds=PROCESSING_DEADLINE_SECONDS),
            completed_at=accepted_at + timedelta(seconds=1),
            sent_at=accepted_at + timedelta(seconds=1),
            line_request_id=f"request-{operation_id}",
        )


@dataclass(slots=True)
class _RaceOutcome:
    value: object | None = None
    error: BaseException | None = None


class LinkedDeliveryBarrierTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.owner = OwnerPrincipal(1)
        self.owner_identity = OwnerIdentitySnapshot(
            UUID("11111111-1111-4111-8111-111111111111")
        )
        self.target = LinkedTargetSnapshot(
            channel_public_id=UUID(
                "22222222-2222-4222-8222-222222222222"
            ),
            channel_label="競合チャネル",
            recipient_public_id=UUID(
                "33333333-3333-4333-8333-333333333333"
            ),
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        self.message = MessageSnapshot(
            subject="競合",
            body="barrier",
            formatted_text="【競合】\n\nbarrier",
            fingerprint="4" * 64,
        )

    # テストケース: accept・finalize・receiptを各2本の独立DB connectionとbarrierで競合させる。
    # 期待値: attemptは1件、終端結果は1件、receipt初回記録は1件に収束し全workerが10秒以内に終了する。
    def test_accept_finalize_and_receipt_barriers_converge_by_cas(
        self,
    ) -> None:
        receipt_digest = "6" * 64
        commands = (
            self._accepted_command(uuid4(), receipt_digest),
            self._accepted_command(uuid4(), receipt_digest),
        )
        accepted_results = self._race(
            tuple(
                lambda command=command: DjangoAttemptRepository(
                    clock=lambda: NOW
                ).accept(command)
                for command in commands
            )
        )
        self.assertEqual(DeliveryAttempt.objects.count(), 1)
        self.assertEqual(
            sum(isinstance(result, AttemptAccepted) for result in accepted_results),
            1,
        )
        self.assertEqual(
            sum(isinstance(result, ExistingAttempt) for result in accepted_results),
            1,
        )
        attempt = DeliveryAttempt.objects.get()

        terminal_snapshots = self._race(
            (
                lambda: DjangoAttemptRepository(clock=lambda: NOW).finalize(
                    attempt.pk,
                    LinePushAccepted("winner-request", None),
                    NOW + timedelta(seconds=1),
                ),
                lambda: DjangoAttemptRepository(clock=lambda: NOW).finalize(
                    attempt.pk,
                    LinePushRejected("permission"),
                    NOW + timedelta(seconds=2),
                ),
            )
        )
        self.assertEqual(terminal_snapshots[0], terminal_snapshots[1])
        self.assertIn(terminal_snapshots[0].status, ("succeeded", "failed"))
        attempt.refresh_from_db()
        winner_fields = self._terminal_fields(attempt)
        opposite_result = (
            LinePushRejected("permission")
            if attempt.status == DeliveryAttempt.Status.SUCCEEDED
            else LinePushAccepted("late-opposite-request", None)
        )

        late_snapshot = DjangoAttemptRepository(clock=lambda: NOW).finalize(
            attempt.pk,
            opposite_result,
            NOW + timedelta(seconds=9),
        )

        attempt.refresh_from_db()
        self.assertEqual(late_snapshot, terminal_snapshots[0])
        self.assertEqual(self._terminal_fields(attempt), winner_fields)
        self.assertIsNone(attempt.active_request_fingerprint)

        receipt_accepted = DjangoAttemptRepository(clock=lambda: NOW).accept(
            self._accepted_command(uuid4(), "7" * 64)
        )
        self.assertIsInstance(receipt_accepted, AttemptAccepted)
        receipt_terminal = DjangoAttemptRepository(
            clock=lambda: NOW
        ).finalize(
            receipt_accepted.attempt_id,
            LinePushAccepted("receipt-request", None),
            NOW + timedelta(seconds=3),
        )
        self.assertEqual(
            receipt_terminal.status,
            DeliveryAttempt.Status.SUCCEEDED,
        )
        receipt_commands = (
            ConfirmReceiptCommand(
                capability_digest="7" * 64,
                channel_public_id=self.target.channel_public_id,
                recipient_public_id=self.target.recipient_public_id,
                webhook_event_id="01J0000000000000000000000A",
                occurred_at=NOW + timedelta(minutes=1),
            ),
            ConfirmReceiptCommand(
                capability_digest="7" * 64,
                channel_public_id=self.target.channel_public_id,
                recipient_public_id=self.target.recipient_public_id,
                webhook_event_id="01J0000000000000000000000B",
                occurred_at=NOW + timedelta(minutes=2),
            ),
        )
        receipt_results = self._race(
            tuple(
                lambda command=command: DjangoAttemptRepository(
                    clock=lambda: NOW
                ).confirm_receipt(command)
                for command in receipt_commands
            )
        )
        self.assertEqual(
            sum(isinstance(result, ReceiptRecorded) for result in receipt_results),
            1,
        )
        self.assertEqual(
            sum(isinstance(result, ReceiptUnchanged) for result in receipt_results),
            1,
        )

    def _accepted_command(
        self,
        operation_id: UUID,
        receipt_digest: str,
    ) -> AcceptedDeliveryCommand:
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
                receipt_requested=True,
            ),
            receipt_commitment=ReceiptCommitment(
                digest=receipt_digest,
                expires_at=NOW + timedelta(hours=24),
            ),
        )

    @staticmethod
    def _terminal_fields(attempt: DeliveryAttempt) -> tuple[object, ...]:
        return (
            attempt.status,
            attempt.completed_at,
            attempt.line_request_id,
            attempt.line_accepted_request_id,
            attempt.failure_type,
            attempt.sent_at,
            attempt.failed_at,
            attempt.active_request_fingerprint,
        )

    def _race(
        self,
        actions: tuple[Callable[[], _T], Callable[[], _T]],
    ) -> tuple[_T, _T]:
        barrier = threading.Barrier(3)
        outcomes = [_RaceOutcome(), _RaceOutcome()]

        def run(index: int, action: Callable[[], _T]) -> None:
            close_old_connections()
            try:
                barrier.wait(timeout=THREAD_DEADLINE_SECONDS)
                outcomes[index].value = action()
            except BaseException as error:
                outcomes[index].error = error
            finally:
                close_old_connections()

        threads = [
            threading.Thread(
                target=run,
                args=(index, action),
                name=f"linked-safety-race-{index}",
                daemon=True,
            )
            for index, action in enumerate(actions)
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=THREAD_DEADLINE_SECONDS)
        for thread in threads:
            thread.join(timeout=THREAD_DEADLINE_SECONDS)
        self.assertEqual(
            [thread.name for thread in threads if thread.is_alive()],
            [],
        )
        for outcome in outcomes:
            if outcome.error is not None:
                raise outcome.error
        return outcomes[0].value, outcomes[1].value


class _MutableClock:
    def __init__(self, current: datetime):
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class _FixedDirectory:
    def __init__(self, target: LiveDeliveryTarget):
        self.target = target

    def resolve(self, owner_identity_id, channel_id, recipient_id):
        return self.target


class _FixedCredentialRepository:
    def get_access_token(self, channel_public_id):
        return CredentialAvailable(AccessToken("safe-fake-token"))


class _DeadlineGateway:
    def __init__(self, clock: _MutableClock):
        self.clock = clock
        self.call_count = 0
        self.atomic_states: list[bool] = []

    def push(self, command):
        self.call_count += 1
        self.atomic_states.append(connection.in_atomic_block)
        self.clock.current += timedelta(
            seconds=PROCESSING_DEADLINE_SECONDS + 1
        )
        return LinePushUnknown("timeout_unknown")


@dataclass(frozen=True, slots=True)
class _DeadlineFixture:
    owner: OwnerPrincipal
    command: SubmitLinkedDelivery
    clock: _MutableClock
    gateway: _DeadlineGateway
    service: DeliveryService


class LinkedDeliveryDeadlineTests(TransactionTestCase):
    reset_sequences = True

    # テストケース: 外部call前のprocessing attemptを受付時刻から30秒ちょうどで二度照会する。
    # 期待値: processing_expiredのunknownへ一度だけCAS確定し、gateway callは0回のままになる。
    def test_processing_expires_once_at_exact_thirty_second_deadline(
        self,
    ) -> None:
        fixture = self._service_fixture()
        accepted = fixture.service.accept_confirmed(fixture.command)
        self.assertIsInstance(accepted, AcceptedLinkedAttempt)
        fixture.clock.current = NOW + timedelta(
            seconds=PROCESSING_DEADLINE_SECONDS
        )

        first = fixture.service.check_linked_status(
            fixture.owner.slot,
            fixture.command.operation_id,
        )
        second = fixture.service.check_linked_status(
            fixture.owner.slot,
            fixture.command.operation_id,
        )

        self.assertEqual(first.status, DeliveryAttempt.Status.UNKNOWN)
        self.assertEqual(first.failure, "processing_expired")
        self.assertEqual(first.completed_at, fixture.clock.current)
        self.assertEqual(second, first)
        self.assertEqual(fixture.gateway.call_count, 0)

    # テストケース: safe gateway fakeが30秒deadlineを越えてtimeout不明を返し、同じ操作を再受付する。
    # 期待値: transaction外の外部callは正確に1回で、timeout_unknownへ確定し自動再送しない。
    def test_timeout_after_processing_deadline_is_unknown_and_never_retried(
        self,
    ) -> None:
        fixture = self._service_fixture()
        service = fixture.service
        command = fixture.command
        clock = fixture.clock
        gateway = fixture.gateway

        accepted = service.accept_confirmed(command)
        self.assertIsInstance(accepted, AcceptedLinkedAttempt)
        attempt = DeliveryAttempt.objects.get(pk=accepted.attempt_id)
        self.assertEqual(
            attempt.processing_expires_at - attempt.accepted_at,
            timedelta(seconds=PROCESSING_DEADLINE_SECONDS),
        )
        executed = service.push_accepted(accepted)
        self.assertIsInstance(executed, LinkedPushExecuted)
        stored = service.finalize_linked_push(executed)
        repeated = service.accept_confirmed(command)

        self.assertEqual(stored.snapshot.status, DeliveryAttempt.Status.UNKNOWN)
        self.assertEqual(stored.snapshot.failure, "timeout_unknown")
        self.assertIsInstance(repeated, ExistingAttempt)
        self.assertEqual(repeated.snapshot, stored.snapshot)
        self.assertEqual(gateway.call_count, 1)
        self.assertEqual(gateway.atomic_states, [False])
        self.assertEqual(
            clock.current - NOW,
            timedelta(seconds=PROCESSING_DEADLINE_SECONDS + 1),
        )

    def _service_fixture(self) -> _DeadlineFixture:
        owner = OwnerPrincipal(7)
        owner_identity = OwnerIdentitySnapshot(uuid4())
        target_snapshot = LinkedTargetSnapshot(
            channel_public_id=uuid4(),
            channel_label="deadlineチャネル",
            recipient_public_id=uuid4(),
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        target = LiveDeliveryTarget(
            owner_identity=owner_identity,
            provider_id="line",
            snapshot=target_snapshot,
            revision=TargetRevision("a" * 64),
            subject=LineSubject("Usafe-fake-subject"),
            delivery_available=True,
        )
        message = MessageSnapshot(
            subject="deadline",
            body="timeout",
            formatted_text="【deadline】\n\ntimeout",
            fingerprint="b" * 64,
        )
        confirmation = ConfirmationSnapshot(
            owner=owner,
            owner_identity=owner_identity,
            channel_public_id=target_snapshot.channel_public_id,
            recipient_public_id=target_snapshot.recipient_public_id,
            target_revision=target.revision,
            message_fingerprint=message.fingerprint,
            receipt_requested=False,
            receipt_expires_at=None,
        )
        command = SubmitLinkedDelivery(
            operation_id=uuid4(),
            confirmation=confirmation,
            message=message,
        )
        clock = _MutableClock(NOW)
        repository = DjangoAttemptRepository(clock=clock)
        gateway = _DeadlineGateway(clock)
        service = DeliveryService(
            clock=clock,
            target_directory=_FixedDirectory(target),
            attempt_repository=repository,
            credential_repository=_FixedCredentialRepository(),
            channel_push_gateway=gateway,
        )
        return _DeadlineFixture(owner, command, clock, gateway, service)
