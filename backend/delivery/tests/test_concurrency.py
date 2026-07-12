import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone

from delivery.formatters import format_message
from delivery.gateway import LinePushAccepted
from delivery.models import DeliveryAttempt
from delivery.services import (
    DeliveryInProgressError,
    DeliveryService,
    SubmitDeliveryCommand,
)


class CountingGateway:
    def __init__(self):
        self.call_count = 0
        self._lock = threading.Lock()

    def push_text(self, command):
        with self._lock:
            self.call_count += 1
        return LinePushAccepted("request-1", None)


def run_with_independent_connection(function):
    close_old_connections()
    try:
        return function()
    finally:
        close_old_connections()


class DeliveryConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    # テストケース: 同じ操作IDと内容を独立DB接続から同時に送信する。
    # 期待値: 両要求が同じ試行へ収束し、gatewayは最大1回だけ呼ばれる。
    def test_concurrent_same_operation_converges_on_one_attempt(self):
        operation_id = uuid4()
        message = format_message("件名", "同一操作")
        command = SubmitDeliveryCommand(operation_id, message)
        gateway = CountingGateway()
        barrier = threading.Barrier(2)

        def submit():
            barrier.wait()
            return DeliveryService(gateway=gateway).submit(command)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _: run_with_independent_connection(submit),
                    range(2),
                )
            )

        self.assertEqual(DeliveryAttempt.objects.filter(operation_id=operation_id).count(), 1)
        self.assertEqual(gateway.call_count, 1)
        self.assertEqual({result.operation_id for result in results}, {operation_id})
        self.assertTrue(all(result.status in ("processing", "succeeded") for result in results))

    # テストケース: 異なる操作IDで同じ内容を独立DB接続から同時に送信する。
    # 期待値: active fingerprint一意制約により外部送信は最大1回になる。
    def test_concurrent_same_active_fingerprint_calls_gateway_once(self):
        message = format_message("件名", "同一内容")
        commands = [SubmitDeliveryCommand(uuid4(), message) for _ in range(2)]

        class BlockingCountingGateway(CountingGateway):
            def __init__(self):
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()

            def push_text(self, command):
                with self._lock:
                    self.call_count += 1
                self.entered.set()
                self.release.wait(timeout=5)
                return LinePushAccepted("request-1", None)

        gateway = BlockingCountingGateway()

        def submit(command):
            try:
                return DeliveryService(gateway=gateway).submit(command).status
            except DeliveryInProgressError:
                return "in_progress"

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                run_with_independent_connection,
                lambda: submit(commands[0]),
            )
            self.assertTrue(gateway.entered.wait(timeout=5))
            second = executor.submit(
                run_with_independent_connection,
                lambda: submit(commands[1]),
            )
            second_result = second.result(timeout=5)
            gateway.release.set()
            results = [first.result(timeout=5), second_result]

        self.assertEqual(gateway.call_count, 1)
        self.assertEqual(DeliveryAttempt.objects.count(), 1)
        self.assertIn("succeeded", results)
        self.assertIn("in_progress", results)

    # テストケース: gateway成功確定と期限切れunknown確定を独立DB接続で競合させる。
    # 期待値: compare-and-setで片方だけが成立し、terminal監査項目が混在しない。
    def test_gateway_and_expiration_compare_and_set_preserve_terminal_state(self):
        now = timezone.now()
        message = format_message("件名", "CAS競合")
        attempt = DeliveryAttempt.objects.create(
            operation_id=uuid4(),
            subject=message.subject,
            body=message.body,
            formatted_text=message.formatted_text,
            content_fingerprint=message.fingerprint,
            active_content_fingerprint=message.fingerprint,
            accepted_at=now - timedelta(seconds=31),
            processing_expires_at=now - timedelta(seconds=1),
        )
        barrier = threading.Barrier(2)

        def finalize():
            barrier.wait()
            DeliveryService(clock=lambda: now)._finalize(
                attempt.pk,
                LinePushAccepted("request-won", None),
            )

        def expire():
            barrier.wait()
            DeliveryService(clock=lambda: now).check_status(attempt.operation_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(run_with_independent_connection, finalize),
                executor.submit(run_with_independent_connection, expire),
            ]
            for future in futures:
                future.result()

        attempt.refresh_from_db()
        if attempt.status == DeliveryAttempt.Status.SUCCEEDED:
            self.assertEqual(attempt.line_request_id, "request-won")
            self.assertEqual(attempt.sent_at, now)
            self.assertIsNone(attempt.failure_type)
            self.assertIsNone(attempt.failed_at)
        else:
            self.assertEqual(attempt.status, DeliveryAttempt.Status.UNKNOWN)
            self.assertEqual(attempt.failure_type, DeliveryAttempt.FailureType.PROCESSING_EXPIRED)
            self.assertEqual(attempt.failed_at, now)
            self.assertIsNone(attempt.sent_at)
            self.assertIsNone(attempt.line_request_id)

    # テストケース: gatewayが完了待ちの間に別のDB接続から受付済み試行を読む。
    # 期待値: 外部通信はtransaction外で実行され、受付行やrow lockを保持しない。
    def test_external_call_occurs_after_acceptance_transaction_commits(self):
        entered_gateway = threading.Event()
        release_gateway = threading.Event()
        operation_id = uuid4()
        message = format_message("件名", "transaction境界")

        class BlockingGateway:
            def push_text(self, command):
                self.in_atomic_block = connection.in_atomic_block
                entered_gateway.set()
                release_gateway.wait(timeout=5)
                return LinePushAccepted("request-1", None)

        gateway = BlockingGateway()

        def submit():
            DeliveryService(gateway=gateway).submit(
                SubmitDeliveryCommand(operation_id, message)
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_with_independent_connection, submit)
            self.assertTrue(entered_gateway.wait(timeout=5))
            visible = DeliveryAttempt.objects.get(operation_id=operation_id)
            self.assertEqual(visible.status, DeliveryAttempt.Status.PROCESSING)
            self.assertFalse(gateway.in_atomic_block)
            release_gateway.set()
            future.result(timeout=5)

        visible.refresh_from_db()
        self.assertEqual(visible.status, DeliveryAttempt.Status.SUCCEEDED)
