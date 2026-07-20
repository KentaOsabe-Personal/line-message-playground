import threading
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from django.test import TransactionTestCase

from linewebhooks.models import WebhookEventReceipt
from linewebhooks.repositories import DjangoEventReceiptRepository
from linewebhooks.tests.support import (
    CHANNEL_ID,
    EVENT_IDS,
    RecordingHandler,
    build_service,
    event,
    signed_payload,
)
from linewebhooks.types import IngressAccepted, ReceiptCandidate, ReceiptStorageFailed


def _with_independent_connection(function):
    close_old_connections()
    try:
        return function()
    finally:
        close_old_connections()


def _candidate(
    webhook_event_id: str,
    *,
    occurred_at_ms: int,
    is_redelivery: bool = False,
) -> ReceiptCandidate:
    return ReceiptCandidate(
        channel_public_id=CHANNEL_ID,
        webhook_event_id=webhook_event_id,
        event_type="message",
        occurred_at_ms=occurred_at_ms,
        is_redelivery=is_redelivery,
        initial_status="processing",
    )


class WebhookIngressConcurrencyIntegrationTests(TransactionTestCase):
    reset_sequences = True

    # テストケース: 同じwebhookEventIdを二つの独立connectionから同時にserviceへ送る
    # 期待値: receipt一件、新規受付監査一件、handler一回へ収束し、勝者のmetadataだけを保持する
    def test_concurrent_same_event_converges_to_single_dispatch(self) -> None:
        start = threading.Barrier(2)
        handler = RecordingHandler()
        first_service, first_audit = build_service(handler=handler)
        second_service, second_audit = build_service(handler=handler)
        first_body, first_signature = signed_payload(
            [event(EVENT_IDS[0], occurred_at_ms=100, is_redelivery=False)]
        )
        second_body, second_signature = signed_payload(
            [event(EVENT_IDS[0], occurred_at_ms=999, is_redelivery=True)]
        )

        def ingest(service, body: bytes, signature: str):
            start.wait(timeout=5)
            return service.ingest(str(CHANNEL_ID), body, signature)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                _with_independent_connection,
                lambda: ingest(first_service, first_body, first_signature),
            )
            second = executor.submit(
                _with_independent_connection,
                lambda: ingest(second_service, second_body, second_signature),
            )
            results = (first.result(timeout=10), second.result(timeout=10))

        self.assertTrue(all(isinstance(result, IngressAccepted) for result in results))
        self.assertEqual(WebhookEventReceipt.objects.count(), 1)
        receipt = WebhookEventReceipt.objects.get()
        self.assertEqual(receipt.status, "processed")
        self.assertEqual(len(handler.events), 1)
        handled = handler.events[0]
        self.assertEqual(handled.occurred_at_ms, receipt.occurred_at_ms)
        self.assertEqual(handled.is_redelivery, receipt.is_redelivery)
        outcomes = [
            entry.outcome
            for entry in (*first_audit.entries, *second_audit.entries)
        ]
        self.assertEqual(outcomes.count("event_accepted"), 1)
        self.assertEqual(outcomes.count("event_duplicate"), 1)
        self.assertEqual(outcomes.count("handler_processed"), 1)

    # テストケース: 一部だけ共通IDを持つ二つのbatchを独立connectionで同時受付する
    # 期待値: 共通IDは一行、各固有IDも一行となり、各decisionは入力順を維持して新規と重複へ分類される
    def test_concurrent_partially_overlapping_batches_preserve_order(self) -> None:
        start = threading.Barrier(2)
        first_batch = (
            _candidate(EVENT_IDS[0], occurred_at_ms=100),
            _candidate(EVENT_IDS[1], occurred_at_ms=101),
        )
        second_batch = (
            _candidate(EVENT_IDS[0], occurred_at_ms=999, is_redelivery=True),
            _candidate(EVENT_IDS[2], occurred_at_ms=102),
        )

        def accept(candidates: tuple[ReceiptCandidate, ...]):
            repository = DjangoEventReceiptRepository()
            start.wait(timeout=5)
            return repository.accept_batch(candidates)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                _with_independent_connection, lambda: accept(first_batch)
            )
            second = executor.submit(
                _with_independent_connection, lambda: accept(second_batch)
            )
            results = (first.result(timeout=10), second.result(timeout=10))

        self.assertTrue(all(isinstance(result, tuple) for result in results))
        self.assertTrue(
            all(not isinstance(result, ReceiptStorageFailed) for result in results)
        )
        first_result, second_result = results
        assert isinstance(first_result, tuple)
        assert isinstance(second_result, tuple)
        self.assertEqual(
            [decision.webhook_event_id for decision in first_result],
            [EVENT_IDS[0], EVENT_IDS[1]],
        )
        self.assertEqual(
            [decision.webhook_event_id for decision in second_result],
            [EVENT_IDS[0], EVENT_IDS[2]],
        )
        common_decisions = (first_result[0], second_result[0])
        self.assertEqual(sum(decision.created for decision in common_decisions), 1)
        self.assertTrue(first_result[1].created)
        self.assertTrue(second_result[1].created)
        self.assertEqual(WebhookEventReceipt.objects.count(), 3)
        common = WebhookEventReceipt.objects.get(webhook_event_id=EVENT_IDS[0])
        self.assertIn(common.occurred_at_ms, (100, 999))
        self.assertEqual(common.is_redelivery, common.occurred_at_ms == 999)

    # テストケース: processing receiptのCAS失敗確定とduplicate読取りを独立connectionで競合させる
    # 期待値: 最終状態はfailedからprocessingへ戻らず、競合側に新規dispatch権を返さない
    def test_finalize_and_duplicate_race_preserves_terminal_state(self) -> None:
        repository = DjangoEventReceiptRepository()
        created = repository.accept_batch(
            (_candidate(EVENT_IDS[0], occurred_at_ms=100),)
        )
        assert isinstance(created, tuple)
        receipt_id = created[0].receipt_id
        start = threading.Barrier(2)

        def finalize():
            start.wait(timeout=5)
            return DjangoEventReceiptRepository().mark_failed(
                receipt_id, "handler_failed"
            )

        def duplicate():
            start.wait(timeout=5)
            return DjangoEventReceiptRepository().accept_batch(
                (
                    _candidate(
                        EVENT_IDS[0], occurred_at_ms=999, is_redelivery=True
                    ),
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            finalized = executor.submit(_with_independent_connection, finalize)
            duplicated = executor.submit(_with_independent_connection, duplicate)
            finalize_result = finalized.result(timeout=10)
            duplicate_result = duplicated.result(timeout=10)

        self.assertEqual(finalize_result, "updated")
        self.assertIsInstance(duplicate_result, tuple)
        assert isinstance(duplicate_result, tuple)
        self.assertFalse(duplicate_result[0].created)
        receipt = WebhookEventReceipt.objects.get(pk=receipt_id)
        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.failure_code, "handler_failed")
        self.assertEqual(receipt.occurred_at_ms, 100)
        self.assertFalse(receipt.is_redelivery)

    # テストケース: failed receiptをserviceから再送する
    # 期待値: handlerを再実行せず、保存済みfailed分類を維持してacceptedへ収束する
    def test_failed_duplicate_never_regains_dispatch_right(self) -> None:
        repository = DjangoEventReceiptRepository()
        created = repository.accept_batch(
            (_candidate(EVENT_IDS[0], occurred_at_ms=100),)
        )
        assert isinstance(created, tuple)
        self.assertEqual(
            repository.mark_failed(created[0].receipt_id, "handler_failed"),
            "updated",
        )
        handler = RecordingHandler()
        service, _ = build_service(
            handler=handler,
            receipt_repository=repository,
        )
        raw_body, signature = signed_payload(
            [event(EVENT_IDS[0], occurred_at_ms=999, is_redelivery=True)]
        )

        result = service.ingest(str(CHANNEL_ID), raw_body, signature)

        self.assertIsInstance(result, IngressAccepted)
        self.assertEqual(handler.events, [])
        receipt = WebhookEventReceipt.objects.get(pk=created[0].receipt_id)
        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.occurred_at_ms, 100)
        self.assertFalse(receipt.is_redelivery)
