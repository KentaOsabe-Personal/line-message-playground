from unittest.mock import patch
from uuid import UUID, uuid4

from django.db import DatabaseError
from django.test import TestCase

from linewebhooks.models import WebhookEventReceipt
from linewebhooks.repositories import DjangoEventReceiptRepository
from linewebhooks.types import ReceiptCandidate, ReceiptStorageFailed


class DjangoEventReceiptRepositoryAcceptanceTests(TestCase):
    def setUp(self) -> None:
        self.repository = DjangoEventReceiptRepository()
        self.channel_public_id = uuid4()

    def _candidate(
        self,
        webhook_event_id: str,
        *,
        channel_public_id: UUID | None = None,
        event_type: str = "message",
        occurred_at_ms: int = 100,
        is_redelivery: bool = False,
        initial_status: str = "processing",
    ) -> ReceiptCandidate:
        return ReceiptCandidate(
            channel_public_id=channel_public_id or self.channel_public_id,
            webhook_event_id=webhook_event_id,
            event_type=event_type,
            occurred_at_ms=occurred_at_ms,
            is_redelivery=is_redelivery,
            initial_status=initial_status,  # type: ignore[arg-type]
        )

    # テストケース: 同じ request 内に重複を含む複数 event を一括受付する
    # 期待値: candidate と同じ順序で最初だけに処理権を与え、ID ごと一行へ収束する
    def test_accept_batch_preserves_order_and_deduplicates_within_request(self) -> None:
        first_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        second_id = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
        candidates = (
            self._candidate(first_id),
            self._candidate(second_id, initial_status="unsupported"),
            self._candidate(
                first_id,
                event_type="follow",
                occurred_at_ms=999,
                is_redelivery=True,
                initial_status="unsupported",
            ),
        )

        result = self.repository.accept_batch(candidates)

        self.assertNotIsInstance(result, ReceiptStorageFailed)
        assert isinstance(result, tuple)
        self.assertEqual(
            [(item.webhook_event_id, item.status, item.created) for item in result],
            [
                (first_id, "processing", True),
                (second_id, "unsupported", True),
                (first_id, "processing", False),
            ],
        )
        self.assertEqual(WebhookEventReceipt.objects.count(), 2)
        first = WebhookEventReceipt.objects.get(webhook_event_id=first_id)
        self.assertEqual(first.event_type, "message")
        self.assertEqual(first.occurred_at_ms, 100)
        self.assertFalse(first.is_redelivery)

    # テストケース: 既存 webhookEventId を異なる metadata で別 request から受付する
    # 期待値: duplicate 判定を返して初回 metadata と保存済み状態を変更しない
    def test_duplicate_request_preserves_first_receipt_snapshot(self) -> None:
        webhook_event_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        first_result = self.repository.accept_batch(
            (self._candidate(webhook_event_id, initial_status="unsupported"),)
        )
        existing = WebhookEventReceipt.objects.get(webhook_event_id=webhook_event_id)

        duplicate_result = self.repository.accept_batch(
            (
                self._candidate(
                    webhook_event_id,
                    channel_public_id=uuid4(),
                    event_type="changed",
                    occurred_at_ms=999,
                    is_redelivery=True,
                ),
            )
        )

        self.assertIsInstance(first_result, tuple)
        self.assertIsInstance(duplicate_result, tuple)
        assert isinstance(duplicate_result, tuple)
        self.assertFalse(duplicate_result[0].created)
        self.assertEqual(duplicate_result[0].status, "unsupported")
        existing.refresh_from_db()
        self.assertEqual(existing.channel_public_id, self.channel_public_id)
        self.assertEqual(existing.event_type, "message")
        self.assertEqual(existing.occurred_at_ms, 100)
        self.assertFalse(existing.is_redelivery)
        self.assertEqual(existing.status, "unsupported")

    # テストケース: batch の二件目の保存で DB 障害が発生する
    # 期待値: 保存失敗を返し、一件目を含む新規行をすべて rollback する
    def test_storage_failure_rolls_back_entire_batch(self) -> None:
        candidates = (
            self._candidate("01ARZ3NDEKTSV4RRFFQ69G5FAV"),
            self._candidate("01ARZ3NDEKTSV4RRFFQ69G5FAW"),
        )
        original_create = self.repository._create_receipt
        call_count = 0

        def fail_second(candidate: ReceiptCandidate) -> WebhookEventReceipt:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise DatabaseError("storage canary")
            return original_create(candidate)

        with patch.object(self.repository, "_create_receipt", side_effect=fail_second):
            result = self.repository.accept_batch(candidates)

        self.assertIsInstance(result, ReceiptStorageFailed)
        self.assertEqual(WebhookEventReceipt.objects.count(), 0)


class DjangoEventReceiptRepositoryFinalizationTests(TestCase):
    def setUp(self) -> None:
        self.repository = DjangoEventReceiptRepository()
        self.channel_public_id = uuid4()

    def _candidate(
        self,
        webhook_event_id: str,
        *,
        initial_status: str = "processing",
        event_type: str = "message",
        occurred_at_ms: int = 100,
        is_redelivery: bool = False,
    ) -> ReceiptCandidate:
        return ReceiptCandidate(
            channel_public_id=self.channel_public_id,
            webhook_event_id=webhook_event_id,
            event_type=event_type,
            occurred_at_ms=occurred_at_ms,
            is_redelivery=is_redelivery,
            initial_status=initial_status,  # type: ignore[arg-type]
        )

    def _accept(self, candidate: ReceiptCandidate) -> WebhookEventReceipt:
        result = self.repository.accept_batch((candidate,))
        assert isinstance(result, tuple)
        return WebhookEventReceipt.objects.get(pk=result[0].receipt_id)

    # テストケース: 二件の processing receipt を handler 成功と失敗で確定する
    # 期待値: processed または handler_failed 付き failed へ一度だけ遷移し metadata は不変となる
    def test_finalizes_processing_receipts_and_preserves_metadata(self) -> None:
        processed = self._accept(
            self._candidate("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        )
        failed = self._accept(
            self._candidate(
                "01ARZ3NDEKTSV4RRFFQ69G5FAW",
                event_type="follow",
                occurred_at_ms=200,
                is_redelivery=True,
            )
        )
        processed_metadata = (
            processed.channel_public_id,
            processed.webhook_event_id,
            processed.event_type,
            processed.occurred_at_ms,
            processed.is_redelivery,
            processed.accepted_at,
        )
        failed_metadata = (
            failed.channel_public_id,
            failed.webhook_event_id,
            failed.event_type,
            failed.occurred_at_ms,
            failed.is_redelivery,
            failed.accepted_at,
        )

        processed_result = self.repository.mark_processed(processed.pk)
        failed_result = self.repository.mark_failed(failed.pk, "handler_failed")

        self.assertEqual(processed_result, "updated")
        self.assertEqual(failed_result, "updated")
        processed.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual(processed.status, "processed")
        self.assertIsNone(processed.failure_code)
        self.assertIsNotNone(processed.completed_at)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.failure_code, "handler_failed")
        self.assertIsNotNone(failed.completed_at)
        self.assertEqual(
            (
                processed.channel_public_id,
                processed.webhook_event_id,
                processed.event_type,
                processed.occurred_at_ms,
                processed.is_redelivery,
                processed.accepted_at,
            ),
            processed_metadata,
        )
        self.assertEqual(
            (
                failed.channel_public_id,
                failed.webhook_event_id,
                failed.event_type,
                failed.occurred_at_ms,
                failed.is_redelivery,
                failed.accepted_at,
            ),
            failed_metadata,
        )

    # テストケース: terminal と unsupported receipt を再確定し、failed event を重複受付する
    # 期待値: 条件付き更新は unchanged となり、duplicate に新規 dispatch 権を返さない
    def test_terminal_receipts_are_monotonic_and_duplicates_stay_existing(self) -> None:
        processed = self._accept(
            self._candidate("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        )
        failed = self._accept(
            self._candidate("01ARZ3NDEKTSV4RRFFQ69G5FAW")
        )
        unsupported = self._accept(
            self._candidate(
                "01ARZ3NDEKTSV4RRFFQ69G5FAX",
                initial_status="unsupported",
            )
        )
        self.assertEqual(self.repository.mark_processed(processed.pk), "updated")
        self.assertEqual(
            self.repository.mark_failed(failed.pk, "handler_failed"), "updated"
        )

        self.assertEqual(
            self.repository.mark_failed(processed.pk, "handler_failed"),
            "unchanged",
        )
        self.assertEqual(self.repository.mark_processed(failed.pk), "unchanged")
        self.assertEqual(self.repository.mark_processed(unsupported.pk), "unchanged")
        duplicate = self.repository.accept_batch(
            (
                self._candidate(
                    failed.webhook_event_id,
                    event_type="changed",
                    occurred_at_ms=999,
                    is_redelivery=True,
                ),
            )
        )
        assert isinstance(duplicate, tuple)
        self.assertFalse(duplicate[0].created)
        self.assertEqual(duplicate[0].status, "failed")

    # テストケース: processing receipt の確定保存で DB 障害が発生する
    # 期待値: failed 結果を返して processing 状態と初回 metadata を維持する
    def test_finalization_storage_failure_leaves_receipt_processing(self) -> None:
        receipt = self._accept(
            self._candidate("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        )

        with patch.object(
            self.repository,
            "_conditional_update",
            side_effect=DatabaseError("storage canary"),
        ):
            result = self.repository.mark_processed(receipt.pk)

        self.assertEqual(result, "failed")
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "processing")
        self.assertIsNone(receipt.failure_code)
        self.assertIsNone(receipt.completed_at)
