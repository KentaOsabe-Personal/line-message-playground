from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar
from uuid import UUID, uuid4

from django.db import close_old_connections
from django.test import TransactionTestCase
from linechannels.tests.reference_fence_support import LOCKED_REFERENCE_FENCE

from delivery.models import DeliveryAttempt
from delivery.repositories import (
    DjangoAttemptRepository,
    build_request_fingerprint,
)
from delivery.types import (
    AcceptedDeliveryCommand,
    AttemptAccepted,
    ConfirmReceiptCommand,
    ExistingAttempt,
    LinkedTargetSnapshot,
    LinePushAccepted,
    LinePushRejected,
    MessageSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
    ReceiptCommitment,
    ReceiptRecorded,
    ReceiptUnchanged,
)


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
_THREAD_TIMEOUT_SECONDS = 10
_T = TypeVar("_T")


@dataclass(slots=True)
class _ThreadOutcome:
    value: object | None = None
    error: BaseException | None = None


class DjangoAttemptRepositoryConcurrencyTests(TransactionTestCase):
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
            channel_label="通知チャネル",
            recipient_public_id=UUID(
                "33333333-3333-4333-8333-333333333333"
            ),
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        self.message = MessageSnapshot(
            subject="競合確認",
            body="同時実行本文",
            formatted_text="【競合確認】\n\n同時実行本文",
            fingerprint="4" * 64,
        )

    # テストケース: 異なるoperationとreceipt候補で同じactive requestを同時に受理する。
    # 期待値: 一方だけが新規attemptとなり、敗者は勝者のoperationとdigestへ収束する。
    def test_accept_race_keeps_one_active_attempt_and_winner_commitment(
        self,
    ) -> None:
        commands = (
            self._command(
                operation_id=uuid4(),
                receipt_digest="6" * 64,
                receipt_expires_at=NOW + timedelta(hours=23),
            ),
            self._command(
                operation_id=uuid4(),
                receipt_digest="7" * 64,
                receipt_expires_at=NOW + timedelta(hours=24),
            ),
        )
        self.assertEqual(
            commands[0].request_fingerprint,
            commands[1].request_fingerprint,
        )
        candidate_pairs = {
            (
                command.receipt_commitment.digest,
                command.receipt_commitment.expires_at,
            )
            for command in commands
        }
        self.assertEqual(len(candidate_pairs), 2)

        results = self._run_concurrently(
            tuple(
                lambda command=command: DjangoAttemptRepository(reference_fence=LOCKED_REFERENCE_FENCE,
                    clock=lambda: NOW
                ).accept(command)
                for command in commands
            )
        )

        accepted = [
            result for result in results if isinstance(result, AttemptAccepted)
        ]
        existing = [
            result for result in results if isinstance(result, ExistingAttempt)
        ]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(existing), 1)
        self.assertEqual(DeliveryAttempt.objects.count(), 1)

        attempt = DeliveryAttempt.objects.get()
        winner_index = next(
            index
            for index, result in enumerate(results)
            if isinstance(result, AttemptAccepted)
        )
        winning_command = commands[winner_index]
        losing_command = commands[1 - winner_index]
        self.assertEqual(attempt.operation_id, winning_command.operation_id)
        self.assertEqual(
            (
                attempt.receipt_token_digest,
                attempt.receipt_expires_at,
            ),
            (
                winning_command.receipt_commitment.digest,
                winning_command.receipt_commitment.expires_at,
            ),
        )
        self.assertEqual(
            attempt.active_request_fingerprint,
            winning_command.request_fingerprint.digest,
        )
        self.assertEqual(
            (
                existing[0].snapshot.operation_id,
                existing[0].snapshot.receipt_expires_at,
            ),
            (
                winning_command.operation_id,
                winning_command.receipt_commitment.expires_at,
            ),
        )
        self.assertFalse(
            DeliveryAttempt.objects.filter(
                receipt_token_digest=(
                    losing_command.receipt_commitment.digest
                )
            ).exists()
        )
        self.assertNotEqual(
            attempt.receipt_expires_at,
            losing_command.receipt_commitment.expires_at,
        )
        self.assertNotEqual(
            attempt.receipt_token_digest,
            losing_command.receipt_commitment.digest,
        )
        self.assertEqual(
            attempt.receipt_expires_at,
            winning_command.receipt_commitment.expires_at,
        )

    # テストケース: 同じprocessing attemptへ異なる終端結果を同時に確定する。
    # 期待値: 最初のCASだけが保存され、両callerは混在しない同じ保存snapshotを得る。
    def test_finalize_race_keeps_first_terminal_and_callers_converge(
        self,
    ) -> None:
        accepted = DjangoAttemptRepository(reference_fence=LOCKED_REFERENCE_FENCE, clock=lambda: NOW).accept(
            self._command(operation_id=uuid4())
        )
        self.assertIsInstance(accepted, AttemptAccepted)
        completed_times = (
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=2),
        )
        terminal_results = (
            LinePushAccepted("request-winner-a", "accepted-winner-a"),
            LinePushRejected("permission"),
        )

        snapshots = self._run_concurrently(
            tuple(
                lambda result=result, completed_at=completed_at:
                DjangoAttemptRepository(reference_fence=LOCKED_REFERENCE_FENCE, clock=lambda: NOW).finalize(
                    accepted.attempt_id,
                    result,
                    completed_at,
                )
                for result, completed_at in zip(
                    terminal_results,
                    completed_times,
                    strict=True,
                )
            )
        )

        attempt = DeliveryAttempt.objects.get(pk=accepted.attempt_id)
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[0].status, attempt.status)
        self.assertEqual(snapshots[0].completed_at, attempt.completed_at)
        if attempt.status == DeliveryAttempt.Status.SUCCEEDED:
            self.assertEqual(attempt.completed_at, completed_times[0])
            self.assertEqual(attempt.line_request_id, "request-winner-a")
            self.assertEqual(
                attempt.line_accepted_request_id,
                "accepted-winner-a",
            )
            self.assertIsNone(attempt.failure_type)
            self.assertIsNone(attempt.failed_at)
        else:
            self.assertEqual(attempt.status, DeliveryAttempt.Status.FAILED)
            self.assertEqual(attempt.completed_at, completed_times[1])
            self.assertEqual(
                attempt.failure_type,
                DeliveryAttempt.FailureType.PERMISSION,
            )
            self.assertEqual(attempt.failed_at, completed_times[1])
            self.assertIsNone(attempt.line_request_id)
            self.assertIsNone(attempt.line_accepted_request_id)
            self.assertIsNone(attempt.sent_at)

    # テストケース: 同じcapabilityへ異なるevent IDと発生時刻を同時に記録する。
    # 期待値: 初回だけがrecordedとなり、敗者は保存済みの日時とevent IDを変更しない。
    def test_receipt_race_keeps_first_event_and_callers_converge(
        self,
    ) -> None:
        digest = "6" * 64
        accepted = DjangoAttemptRepository(reference_fence=LOCKED_REFERENCE_FENCE, clock=lambda: NOW).accept(
            self._command(
                operation_id=uuid4(),
                receipt_digest=digest,
            )
        )
        self.assertIsInstance(accepted, AttemptAccepted)
        completed_at = NOW + timedelta(seconds=30)
        terminal = DjangoAttemptRepository(reference_fence=LOCKED_REFERENCE_FENCE, clock=lambda: NOW).finalize(
            accepted.attempt_id,
            LinePushAccepted(
                "receipt-race-request-id",
                "receipt-race-accepted-request-id",
            ),
            completed_at,
        )
        self.assertEqual(terminal.status, DeliveryAttempt.Status.SUCCEEDED)
        commands = (
            self._receipt_command(
                digest=digest,
                event_id="01J0000000000000000000000A",
                occurred_at=NOW + timedelta(minutes=1),
            ),
            self._receipt_command(
                digest=digest,
                event_id="01J0000000000000000000000B",
                occurred_at=NOW + timedelta(minutes=2),
            ),
        )

        results = self._run_concurrently(
            tuple(
                lambda command=command: DjangoAttemptRepository(reference_fence=LOCKED_REFERENCE_FENCE,
                    clock=lambda: NOW
                ).confirm_receipt(command)
                for command in commands
            )
        )

        self.assertEqual(
            sum(isinstance(result, ReceiptRecorded) for result in results),
            1,
        )
        self.assertEqual(
            sum(isinstance(result, ReceiptUnchanged) for result in results),
            1,
        )
        attempt = DeliveryAttempt.objects.get(pk=accepted.attempt_id)
        self.assertEqual(attempt.status, DeliveryAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.completed_at, completed_at)
        self.assertEqual(attempt.sent_at, completed_at)
        self.assertEqual(
            attempt.line_request_id,
            "receipt-race-request-id",
        )
        self.assertEqual(
            attempt.line_accepted_request_id,
            "receipt-race-accepted-request-id",
        )
        stored_pair = (
            attempt.receipt_webhook_event_id,
            attempt.receipt_confirmed_at,
        )
        self.assertIn(
            stored_pair,
            {
                (command.webhook_event_id, command.occurred_at)
                for command in commands
            },
        )
        for result in results:
            self.assertEqual(
                result.snapshot.receipt_webhook_event_id,
                attempt.receipt_webhook_event_id,
            )
            self.assertEqual(
                result.snapshot.receipt_confirmed_at,
                attempt.receipt_confirmed_at,
            )
            self.assertEqual(
                result.snapshot.status,
                DeliveryAttempt.Status.SUCCEEDED,
            )
            self.assertEqual(result.snapshot.completed_at, completed_at)
            self.assertEqual(
                result.snapshot.line_request_id,
                "receipt-race-request-id",
            )
            self.assertEqual(
                result.snapshot.line_accepted_request_id,
                "receipt-race-accepted-request-id",
            )

    def _command(
        self,
        *,
        operation_id: UUID,
        receipt_digest: str | None = None,
        receipt_expires_at: datetime | None = None,
    ) -> AcceptedDeliveryCommand:
        commitment = (
            ReceiptCommitment(
                digest=receipt_digest,
                expires_at=(
                    receipt_expires_at
                    if receipt_expires_at is not None
                    else NOW + timedelta(hours=24)
                ),
            )
            if receipt_digest is not None
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
                receipt_requested=commitment is not None,
            ),
            receipt_commitment=commitment,
        )

    def _receipt_command(
        self,
        *,
        digest: str,
        event_id: str,
        occurred_at: datetime,
    ) -> ConfirmReceiptCommand:
        return ConfirmReceiptCommand(
            capability_digest=digest,
            channel_public_id=self.target.channel_public_id,
            recipient_public_id=self.target.recipient_public_id,
            webhook_event_id=event_id,
            occurred_at=occurred_at,
        )

    def _run_concurrently(
        self,
        actions: tuple[Callable[[], _T], Callable[[], _T]],
    ) -> tuple[_T, _T]:
        barrier = threading.Barrier(len(actions) + 1)
        finished = threading.Event()
        outcomes = [_ThreadOutcome() for _ in actions]

        def run(index: int, action: Callable[[], _T]) -> None:
            close_old_connections()
            try:
                barrier.wait(timeout=_THREAD_TIMEOUT_SECONDS)
                outcomes[index].value = action()
            except BaseException as error:
                outcomes[index].error = error
            finally:
                close_old_connections()
                if all(
                    outcome.value is not None or outcome.error is not None
                    for outcome in outcomes
                ):
                    finished.set()

        threads = [
            threading.Thread(
                target=run,
                args=(index, action),
                name=f"repository-race-{index}",
                daemon=True,
            )
            for index, action in enumerate(actions)
        ]
        main_error: BaseException | None = None
        alive_workers: list[str] = []
        try:
            for thread in threads:
                thread.start()
            barrier.wait(timeout=_THREAD_TIMEOUT_SECONDS)
            if not finished.wait(timeout=_THREAD_TIMEOUT_SECONDS):
                main_error = TimeoutError(
                    "競合workerが制限時間内に完了しませんでした"
                )
        except BaseException as error:
            main_error = error
        finally:
            if main_error is not None:
                barrier.abort()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(timeout=_THREAD_TIMEOUT_SECONDS)
            alive_workers = [
                thread.name for thread in threads if thread.is_alive()
            ]
        if alive_workers:
            raise AssertionError(
                "競合workerが終了せず残っています: "
                + ", ".join(alive_workers)
            ) from main_error
        if main_error is not None:
            raise main_error
        for outcome in outcomes:
            if outcome.error is not None:
                raise outcome.error
        return outcomes[0].value, outcomes[1].value
