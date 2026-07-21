from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import TestCase

from linefriendships.models import FriendshipSyncAudit


class FriendshipSyncAuditModelTests(TestCase):
    def create_audit(self, **overrides):
        values = {
            "channel_public_id": uuid4(),
            "webhook_event_id": "01J00000000000000000000000",
            "event_type": "follow",
            "occurred_at_ms": 1,
            "outcome": "applied",
            "is_unblocked": False,
        }
        values.update(overrides)
        return FriendshipSyncAudit.objects.create(**values)

    # テストケース: 友だち同期監査modelのschema fieldを列挙する
    # 期待値: safe metadataだけを持ち、identity・subject・payload・error detailを保持しない
    def test_schema_contains_only_pii_free_audit_fields(self):
        self.assertEqual(
            {field.name for field in FriendshipSyncAudit._meta.get_fields()},
            {
                "id",
                "channel_public_id",
                "webhook_event_id",
                "event_type",
                "occurred_at_ms",
                "outcome",
                "is_unblocked",
                "recorded_at",
            },
        )

    # テストケース: 同じevent IDに対する安全な処理結果を複数回記録する
    # 期待値: event IDを一意化せず、appendされた各試行を時刻とともに保持する
    def test_audit_rows_are_appendable_for_repeated_event_id(self):
        first = self.create_audit(outcome="applied")
        second = self.create_audit(
            channel_public_id=first.channel_public_id,
            webhook_event_id=first.webhook_event_id,
            outcome="duplicate",
            is_unblocked=None,
        )

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(
            list(
                FriendshipSyncAudit.objects.order_by("recorded_at", "pk").values_list(
                    "outcome", flat=True
                )
            ),
            ["applied", "duplicate"],
        )

    # テストケース: 不正なevent type・outcome・時刻を監査行へ保存する
    # 期待値: DB constraintが安全な分類と非負timestampだけを受理する
    def test_database_rejects_invalid_audit_classifications_and_timestamp(self):
        invalid_values = (
            {"event_type": "message"},
            {"outcome": "failed_with_detail"},
            {"occurred_at_ms": -1},
        )

        for overrides in invalid_values:
            with self.subTest(overrides=overrides):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.create_audit(**overrides)

    # テストケース: unfollow監査へfollow専用のunblock補助値を保存する
    # 期待値: unfollowではnullだけを許可し、followではbooleanまたはnullを許可する
    def test_unfollow_cannot_store_unblock_flag(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_audit(event_type="unfollow", is_unblocked=False)

        audit = self.create_audit(event_type="unfollow", is_unblocked=None)
        self.assertIsNone(audit.is_unblocked)

    # テストケース: 監査検索用indexのfield順を参照する
    # 期待値: eventとchannelの双方をrecorded_at付きで追跡できる
    def test_audit_indexes_support_safe_correlation(self):
        self.assertEqual(
            {tuple(index.fields) for index in FriendshipSyncAudit._meta.indexes},
            {
                ("webhook_event_id", "recorded_at"),
                ("channel_public_id", "recorded_at"),
            },
        )
