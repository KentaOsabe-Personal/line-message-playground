from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.test import TestCase

from lineinteractions.models import InteractionAudit
from lineinteractions.repositories import DjangoInteractionAuditRepository
from lineinteractions.types import InteractionAuditRecord


class InteractionAuditRepositoryTests(TestCase):
    def setUp(self):
        self.repository = DjangoInteractionAuditRepository()
        self.record = InteractionAuditRecord(
            channel_public_id=uuid4(),
            webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            event_type="message",
            operation_kind="command",
            operation_identifier="connectivity_ping_v1",
            interaction_outcome="command_processed",
            reply_outcome="accepted",
        )

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
