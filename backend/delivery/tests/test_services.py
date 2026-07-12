from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from delivery.formatters import format_message
from delivery.gateway import LinePushAccepted, LinePushRejected, LinePushUnknown
from delivery.models import DeliveryAttempt
from delivery.services import (
    DeliveryInProgressError,
    DeliveryService,
    OperationIdReusedError,
    SubmitDeliveryCommand,
)


class FakeGateway:
    def __init__(self, result):
        self.result = result
        self.commands = []

    def push_text(self, command):
        self.commands.append(command)
        return self.result


class DeliveryServiceSubmitTests(TestCase):
    # テストケース: 新規操作を成功結果で送信する
    # 期待値: 受付行が確定され、gatewayを1回だけ呼び、成功監査項目を保存する
    def test_submit_new_operation_calls_gateway_once_and_persists_success(self):
        gateway = FakeGateway(LinePushAccepted("request-1", None))
        service = DeliveryService(gateway=gateway)
        operation_id = uuid4()
        message = format_message("件名", "本文")

        result = service.submit(SubmitDeliveryCommand(operation_id, message))

        attempt = DeliveryAttempt.objects.get(operation_id=operation_id)
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(result.created)
        self.assertEqual(len(gateway.commands), 1)
        self.assertEqual(gateway.commands[0].retry_key, operation_id)
        self.assertEqual(gateway.commands[0].text, message.formatted_text)
        self.assertEqual(attempt.status, DeliveryAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.line_request_id, "request-1")
        self.assertIsNotNone(attempt.sent_at)

    # テストケース: 同じ操作IDと内容を再送する
    # 期待値: 既存結果を返し、gateway呼出しを増やさない
    def test_submit_same_operation_and_content_returns_existing_result(self):
        gateway = FakeGateway(LinePushAccepted("request-1", None))
        service = DeliveryService(gateway=gateway)
        command = SubmitDeliveryCommand(uuid4(), format_message("件名", "本文"))

        first = service.submit(command)
        second = service.submit(command)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(len(gateway.commands), 1)

    # テストケース: 既存操作IDを異なる内容で再利用する
    # 期待値: 操作ID再利用として拒否し、gatewayを追加で呼ばない
    def test_submit_reused_operation_with_different_content_is_rejected(self):
        gateway = FakeGateway(LinePushAccepted("request-1", None))
        service = DeliveryService(gateway=gateway)
        operation_id = uuid4()
        service.submit(SubmitDeliveryCommand(operation_id, format_message("件名", "本文")))

        with self.assertRaises(OperationIdReusedError):
            service.submit(SubmitDeliveryCommand(operation_id, format_message("別件", "本文")))

        self.assertEqual(len(gateway.commands), 1)

    # テストケース: 別操作IDで同じ内容が処理中である
    # 期待値: 同一内容処理中として拒否し、gatewayを呼ばない
    def test_submit_same_active_content_with_different_operation_is_rejected(self):
        message = format_message("件名", "本文")
        now = timezone.now()
        DeliveryAttempt.objects.create(
            operation_id=uuid4(),
            subject=message.subject,
            body=message.body,
            formatted_text=message.formatted_text,
            content_fingerprint=message.fingerprint,
            active_content_fingerprint=message.fingerprint,
            accepted_at=now,
            processing_expires_at=now + timedelta(seconds=30),
        )
        gateway = FakeGateway(LinePushAccepted("request-1", None))

        with self.assertRaises(DeliveryInProgressError):
            DeliveryService(gateway=gateway).submit(
                SubmitDeliveryCommand(uuid4(), message)
            )

        self.assertEqual(gateway.commands, [])

    # テストケース: gatewayが確定失敗または結果不明を返す
    # 期待値: 対応するterminal状態、失敗種別、完了日時を一度だけ保存する
    def test_submit_persists_unsuccessful_gateway_results(self):
        cases = (
            (LinePushRejected("authentication"), "failed", "authentication"),
            (LinePushUnknown("timeout_unknown"), "unknown", "timeout_unknown"),
        )
        for gateway_result, status, failure_type in cases:
            with self.subTest(status=status):
                operation_id = uuid4()
                gateway = FakeGateway(gateway_result)
                result = DeliveryService(gateway=gateway).submit(
                    SubmitDeliveryCommand(operation_id, format_message("件名", str(operation_id)))
                )
                attempt = DeliveryAttempt.objects.get(operation_id=operation_id)
                self.assertEqual(result.status, status)
                self.assertEqual(attempt.failure_type, failure_type)
                self.assertIsNotNone(attempt.failed_at)
                self.assertIsNotNone(attempt.completed_at)

    # テストケース: gateway完了前に別処理が試行をterminal状態へ確定する
    # 期待値: 遅延したgateway結果は先行terminal状態と監査項目を上書きしない
    def test_finalize_does_not_overwrite_existing_terminal_result(self):
        message = format_message("件名", "本文")
        accepted_at = timezone.now()
        attempt = DeliveryAttempt.objects.create(
            operation_id=uuid4(),
            subject=message.subject,
            body=message.body,
            formatted_text=message.formatted_text,
            content_fingerprint=message.fingerprint,
            active_content_fingerprint=message.fingerprint,
            accepted_at=accepted_at,
            processing_expires_at=accepted_at + timedelta(seconds=30),
        )
        first_completed_at = accepted_at + timedelta(seconds=1)
        attempt.mark_unsuccessful(
            status=DeliveryAttempt.Status.UNKNOWN,
            failure_type=DeliveryAttempt.FailureType.PROCESSING_EXPIRED,
            completed_at=first_completed_at,
        )

        DeliveryService(clock=lambda: accepted_at + timedelta(seconds=2))._finalize(
            attempt.pk,
            LinePushAccepted("late-request", None),
        )

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, DeliveryAttempt.Status.UNKNOWN)
        self.assertEqual(attempt.failure_type, "processing_expired")
        self.assertEqual(attempt.completed_at, first_completed_at)
        self.assertIsNone(attempt.line_request_id)


class DeliveryServiceStatusTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.gateway = FakeGateway(LinePushAccepted("unused", None))
        self.service = DeliveryService(gateway=self.gateway, clock=lambda: self.now)

    def create_processing_attempt(self, *, expires_at):
        message = format_message("件名", str(uuid4()))
        return DeliveryAttempt.objects.create(
            operation_id=uuid4(),
            subject=message.subject,
            body=message.body,
            formatted_text=message.formatted_text,
            content_fingerprint=message.fingerprint,
            active_content_fingerprint=message.fingerprint,
            accepted_at=self.now - timedelta(seconds=5),
            processing_expires_at=expires_at,
        )

    # テストケース: 存在しない操作IDの状態を確認する
    # 期待値: Noneを返し、試行作成もgateway呼出しも行わない
    def test_check_status_missing_operation_has_no_side_effects(self):
        result = self.service.check_status(uuid4())

        self.assertIsNone(result)
        self.assertEqual(DeliveryAttempt.objects.count(), 0)
        self.assertEqual(self.gateway.commands, [])

    # テストケース: 処理期限内の操作状態を確認する
    # 期待値: 受付・期限日時を持つprocessingを返し、gatewayを呼ばない
    def test_check_status_returns_processing_before_expiration(self):
        attempt = self.create_processing_attempt(
            expires_at=self.now + timedelta(seconds=1)
        )

        result = self.service.check_status(attempt.operation_id)

        self.assertEqual(result.status, "processing")
        self.assertEqual(result.accepted_at, attempt.accepted_at)
        self.assertEqual(result.processing_expires_at, attempt.processing_expires_at)
        self.assertEqual(self.gateway.commands, [])

    # テストケース: 処理期限を過ぎた操作状態を確認する
    # 期待値: gatewayを呼ばずprocessing_expiredのunknownへ一度だけ確定する
    def test_check_status_expires_processing_attempt_once(self):
        attempt = self.create_processing_attempt(
            expires_at=self.now - timedelta(microseconds=1)
        )

        first = self.service.check_status(attempt.operation_id)
        second = self.service.check_status(attempt.operation_id)

        attempt.refresh_from_db()
        self.assertEqual(first.status, "unknown")
        self.assertEqual(second.status, "unknown")
        self.assertEqual(attempt.failure_type, "processing_expired")
        self.assertEqual(attempt.failed_at, self.now)
        self.assertEqual(attempt.completed_at, self.now)
        self.assertEqual(self.gateway.commands, [])

    # テストケース: 期限切れ確定より先にgateway結果がterminal状態を確定する
    # 期待値: 状態確認は先行terminal状態を上書きせず、その結果を返す
    def test_check_status_preserves_terminal_result_won_by_gateway(self):
        attempt = self.create_processing_attempt(
            expires_at=self.now - timedelta(microseconds=1)
        )
        completed_at = self.now - timedelta(seconds=1)
        attempt.mark_succeeded(completed_at=completed_at, line_request_id="request-1")

        result = self.service.check_status(attempt.operation_id)

        attempt.refresh_from_db()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(attempt.completed_at, completed_at)
        self.assertEqual(attempt.line_request_id, "request-1")
        self.assertEqual(self.gateway.commands, [])
