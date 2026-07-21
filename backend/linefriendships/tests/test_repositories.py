from unittest import mock
from uuid import uuid4

from django.db import DatabaseError, transaction
from django.test import TransactionTestCase

from linefriendships.models import FriendshipSyncAudit
from linefriendships.repositories import (
    DjangoFriendshipAuditRepository,
    FriendshipAuditStorageError,
)
from linefriendships.types import FriendshipAuditRecord


class FriendshipAuditRepositoryTests(TransactionTestCase):
    def setUp(self):
        self.repository = DjangoFriendshipAuditRepository()

    def audit(self, *, outcome="applied", event_type="follow", is_unblocked=True):
        return FriendshipAuditRecord(
            channel_public_id=uuid4(),
            webhook_event_id="01J00000000000000000000000",
            event_type=event_type,
            occurred_at_ms=123,
            outcome=outcome,
            is_unblocked=is_unblocked,
        )

    # テストケース: 設計済みの全safe outcomeを監査repositoryへ渡す
    # 期待値: PIIを加えず各処理試行をappend-only rowとして保存する
    def test_appends_every_safe_outcome(self):
        outcomes = (
            "applied",
            "state_maintained",
            "stale",
            "duplicate",
            "unlinked",
            "unresolvable",
            "out_of_scope",
            "invalid",
        )

        with transaction.atomic():
            for outcome in outcomes:
                self.repository.record(self.audit(outcome=outcome))

        self.assertEqual(
            list(
                FriendshipSyncAudit.objects.order_by("pk").values_list(
                    "outcome", flat=True
                )
            ),
            list(outcomes),
        )

    # テストケース: unfollowのsafe audit recordを永続化する
    # 期待値: unblock補助値をnullとしevent IDでreceiptと相関できる
    def test_records_unfollow_without_unblock_flag(self):
        record = self.audit(
            event_type="unfollow",
            is_unblocked=None,
            outcome="state_maintained",
        )
        with transaction.atomic():
            self.repository.record(record)

        stored = FriendshipSyncAudit.objects.get()
        self.assertEqual(stored.channel_public_id, record.channel_public_id)
        self.assertEqual(stored.webhook_event_id, record.webhook_event_id)
        self.assertEqual(stored.event_type, "unfollow")
        self.assertEqual(stored.occurred_at_ms, 123)
        self.assertEqual(stored.outcome, "state_maintained")
        self.assertIsNone(stored.is_unblocked)

    # テストケース: 監査insertがDatabaseErrorで失敗する
    # 期待値: storage failureへ縮約して呼び出し元へ伝播する
    def test_translates_insert_failure_to_storage_error(self):
        with mock.patch.object(
            FriendshipSyncAudit.objects,
            "using",
            side_effect=DatabaseError("sensitive detail"),
        ):
            with self.assertRaises(FriendshipAuditStorageError) as raised:
                with transaction.atomic():
                    self.repository.record(self.audit())

        self.assertEqual(raised.exception.code, "storage_unavailable")
        self.assertNotIn("sensitive detail", str(raised.exception))

    # テストケース: transaction外で監査appendを呼び出す
    # 期待値: programming errorとして拒否し部分確定を許さない
    def test_requires_active_transaction(self):
        with self.assertRaises(RuntimeError):
            self.repository.record(self.audit())
