from unittest.mock import Mock, patch
from uuid import uuid4

from django.db import DatabaseError
from django.test import TestCase

from lineinteractions.models import InteractionAudit
from lineinteractions.repositories import DjangoInteractionAuditRepository
from lineinteractions.types import InteractionAuditRecord
from linechannels.reference_fence import ReferenceFenceResult
from linechannels.tests.reference_fence_support import LOCKED_REFERENCE_FENCE


class InteractionAuditRepositoryTests(TestCase):
    def setUp(self):
        self.repository = DjangoInteractionAuditRepository(LOCKED_REFERENCE_FENCE)
        self.record = InteractionAuditRecord(
            channel_public_id=uuid4(),
            webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            event_type="message",
            operation_kind="command",
            operation_identifier="connectivity_ping_v1",
            interaction_outcome="command_processed",
            reply_outcome="accepted",
        )

    # テストケース: interaction audit前のchannel fenceが失敗する
    # 期待値: auditを作成せずchannel不在とstorage分類をそのまま返す
    def test_reference_fence_failure_prevents_audit_insert(self):
        for status in (
            "channel_not_found",
            "storage_retryable",
            "storage_unavailable",
        ):
            fence = Mock()
            fence.lock_existing.return_value = ReferenceFenceResult(status)
            repository = DjangoInteractionAuditRepository(
                reference_fence=fence
            )

            with self.subTest(status=status):
                result = repository.record(self.record)

            self.assertEqual(result, status)
            self.assertEqual(InteractionAudit.objects.count(), 0)

    # テストケース: reply/action前にfallback監査を予約し外部結果後に確定する
    # 期待値: fence済み参照行が先に存在し、同じevent行だけがsafe outcomeへ更新される
    def test_reserves_reference_before_external_result_and_replaces_it(self):
        reserved = InteractionAuditRecord(
            channel_public_id=self.record.channel_public_id,
            webhook_event_id=self.record.webhook_event_id,
            event_type="message",
            operation_kind="command",
            operation_identifier="connectivity_ping_v1",
            interaction_outcome="processing_failed",
            reply_outcome="not_started",
        )

        self.assertEqual(self.repository.reserve(reserved), "recorded")
        stored = InteractionAudit.objects.get()
        self.assertEqual(stored.interaction_outcome, "processing_failed")

        self.assertEqual(
            self.repository.replace_reserved(self.record),
            "recorded",
        )
        stored.refresh_from_db()
        self.assertEqual(stored.interaction_outcome, "command_processed")
        self.assertEqual(stored.reply_outcome, "accepted")
        self.assertEqual(InteractionAudit.objects.count(), 1)

    # テストケース: PII-free audit recordを一件保存する
    # 期待値: recordedを返し安全fieldだけを永続化する
    def test_records_safe_audit_once(self):
        self.assertEqual(self.repository.record(self.record), "recorded")

        stored = InteractionAudit.objects.get()
        self.assertEqual(stored.webhook_event_id, self.record.webhook_event_id)
        self.assertEqual(stored.operation_identifier, "connectivity_ping_v1")

    # テストケース: 重複event IDまたはDB例外で監査保存する
    # 期待値: 生例外を公開せずfailedへ縮約する
    def test_storage_failures_are_safe(self):
        self.assertEqual(self.repository.record(self.record), "recorded")
        self.assertEqual(self.repository.record(self.record), "failed")
        with patch.object(
            InteractionAudit.objects,
            "using",
            side_effect=DatabaseError("secret-canary"),
        ):
            self.assertEqual(self.repository.record(self.record), "failed")
