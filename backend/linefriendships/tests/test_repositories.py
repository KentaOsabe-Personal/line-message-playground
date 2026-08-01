from unittest import mock
from uuid import uuid4

from django.db import DatabaseError, transaction
from django.test import TransactionTestCase

from linefriendships.models import FriendshipSyncAudit
from linefriendships.repositories import (
    DjangoFriendshipAuditRepository,
    FriendshipAuditProgrammingError,
    FriendshipAuditStorageError,
)
from linefriendships.types import FriendshipAuditRecord
from linechannels.models import LineChannel
from linechannels.reference_fence import (
    DjangoChannelReferenceFence,
    ReferenceFenceResult,
)
from linechannels.tests.reference_fence_support import LOCKED_REFERENCE_FENCE


class FriendshipAuditRepositoryTests(TransactionTestCase):
    def setUp(self):
        self.repository = DjangoFriendshipAuditRepository(LOCKED_REFERENCE_FENCE)

    def audit(self, *, outcome="applied", event_type="follow", is_unblocked=True):
        return FriendshipAuditRecord(
            channel_public_id=uuid4(),
            webhook_event_id="01J00000000000000000000000",
            event_type=event_type,
            occurred_at_ms=123,
            outcome=outcome,
            is_unblocked=is_unblocked,
        )

    # テストケース: projectionと同じtransactionでaudit前のfenceが失敗する
    # 期待値: auditを作成せずchannel不在とstorage分類を失わず返す
    def test_reference_fence_failure_prevents_audit_insert(self):
        for status, expected in (
            ("channel_not_found", "channel_not_found"),
            ("storage_retryable", "retryable"),
            ("storage_unavailable", "storage_unavailable"),
        ):
            fence = mock.Mock()
            fence.lock_existing.return_value = ReferenceFenceResult(status)
            repository = DjangoFriendshipAuditRepository(
                reference_fence=fence
            )

            with self.subTest(status=status), self.assertRaises(
                FriendshipAuditStorageError
            ) as raised, transaction.atomic():
                repository.record(self.audit())

            self.assertEqual(raised.exception.code, expected)
            self.assertEqual(FriendshipSyncAudit.objects.count(), 0)

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

    # テストケース: 公開呼出し側がfence capabilityを直接偽造する
    # 期待値: 同一transaction内でもauditを作成せずcontract違反として拒否する
    def test_rejects_forged_reference_capability(self):
        with transaction.atomic(), self.assertRaises(
            FriendshipAuditProgrammingError
        ) as raised:
            self.repository.record_after_fence(self.audit(), object())

        self.assertEqual(str(raised.exception), "invalid_reference_lock")
        self.assertEqual(FriendshipSyncAudit.objects.count(), 0)

    # テストケース: 別repositoryが同じtransactionで取得したcapabilityを流用する
    # 期待値: 発行repository identity不一致を拒否してauditを作成しない
    def test_rejects_capability_issued_by_another_repository(self):
        other = DjangoFriendshipAuditRepository(LOCKED_REFERENCE_FENCE)
        record = self.audit()

        with transaction.atomic():
            locked = other.lock_reference(record.channel_public_id)
            with self.assertRaises(FriendshipAuditProgrammingError) as raised:
                self.repository.record_after_fence(record, locked)

        self.assertEqual(str(raised.exception), "invalid_reference_lock")
        self.assertEqual(FriendshipSyncAudit.objects.count(), 0)

    # テストケース: transaction Aで取得したcapabilityをtransaction Bへ持ち越す
    # 期待値: 間でchannelが削除されても孤立auditを作成しない
    def test_rejects_capability_reused_in_later_transaction(self):
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id="U" + uuid4().hex,
            label="capability lifetime",
            provider_id="001234",
            is_active=True,
        )
        repository = DjangoFriendshipAuditRepository(DjangoChannelReferenceFence())
        record = FriendshipAuditRecord(
            channel_public_id=channel.public_id,
            webhook_event_id="01J00000000000000000000000",
            event_type="follow",
            occurred_at_ms=123,
            outcome="unlinked",
            is_unblocked=True,
        )
        with transaction.atomic():
            locked = repository.lock_reference(record.channel_public_id)

        channel.delete()

        with transaction.atomic(), self.assertRaises(
            FriendshipAuditProgrammingError
        ) as raised:
            repository.record_after_fence(record, locked)

        self.assertEqual(str(raised.exception), "invalid_reference_lock")
        self.assertEqual(FriendshipSyncAudit.objects.count(), 0)

    # テストケース: 同じAtomic context managerをtransaction A/Bで再利用する
    # 期待値: A終了時のon_commit登録消失を検出し、Bでcapabilityを再利用できない
    def test_rejects_capability_when_atomic_object_is_reused(self):
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id="U" + uuid4().hex,
            label="reused atomic capability",
            provider_id="001234",
            is_active=True,
        )
        repository = DjangoFriendshipAuditRepository(DjangoChannelReferenceFence())
        record = FriendshipAuditRecord(
            channel_public_id=channel.public_id,
            webhook_event_id="01J00000000000000000000000",
            event_type="follow",
            occurred_at_ms=123,
            outcome="unlinked",
            is_unblocked=True,
        )
        reusable_atomic = transaction.atomic()

        with reusable_atomic:
            locked = repository.lock_reference(record.channel_public_id)

        channel.delete()

        with reusable_atomic, self.assertRaises(
            FriendshipAuditProgrammingError
        ) as raised:
            repository.record_after_fence(record, locked)

        self.assertEqual(str(raised.exception), "invalid_reference_lock")
        self.assertEqual(FriendshipSyncAudit.objects.count(), 0)

    # テストケース: capability取得transactionをrollback後に同じAtomicで再利用する
    # 期待値: rollbackでon_commit登録が破棄され、次transactionでauditを作成しない
    def test_rejects_capability_after_rollback_with_reused_atomic(self):
        record = self.audit()
        reusable_atomic = transaction.atomic()

        with self.assertRaises(RuntimeError):
            with reusable_atomic:
                locked = self.repository.lock_reference(record.channel_public_id)
                raise RuntimeError("rollback")

        with reusable_atomic, self.assertRaises(
            FriendshipAuditProgrammingError
        ) as raised:
            self.repository.record_after_fence(record, locked)

        self.assertEqual(str(raised.exception), "invalid_reference_lock")
        self.assertEqual(FriendshipSyncAudit.objects.count(), 0)

    # テストケース: 正規capabilityへ異なるchannelのauditを組み合わせる
    # 期待値: channel binding不一致を拒否してauditを作成しない
    def test_rejects_capability_for_another_channel(self):
        locked_channel = uuid4()
        record = self.audit()

        with transaction.atomic():
            locked = self.repository.lock_reference(locked_channel)
            with self.assertRaises(FriendshipAuditProgrammingError) as raised:
                self.repository.record_after_fence(record, locked)

        self.assertEqual(str(raised.exception), "invalid_reference_lock")
        self.assertEqual(FriendshipSyncAudit.objects.count(), 0)
