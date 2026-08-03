from datetime import timedelta
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from linechannels.models import ChannelDeactivationState, LineChannel


class ChannelDeactivationStateModelTests(TestCase):
    def make_channel(self):
        return LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id="U" + uuid4().hex,
            label="無効化状態の検証用",
            provider_id="0012345678",
            is_active=True,
        )

    def make_state(self, channel, **overrides):
        values = {
            "line_channel": channel,
            "operation_id": uuid4(),
            "owner_identity_public_id": uuid4(),
            "provider_id": "0012345678",
            "expected_channel_revision": channel.updated_at,
            "status": "checking",
        }
        values.update(overrides)
        return ChannelDeactivationState.objects.create(**values)

    # テストケース: チャネルごとの現在の無効化意図を保存する。
    # 期待値: owner・provider・revision・operation・時刻だけを持つ一対一projectionになる。
    def test_current_deactivation_intent_is_one_to_one_and_operation_is_unique(self):
        channel = self.make_channel()
        state = self.make_state(channel)

        self.assertEqual(state.pk, channel.pk)
        self.assertEqual(state.operation_id.version, 4)
        self.assertEqual(state.provider_id, "0012345678")
        self.assertIsNotNone(state.accepted_at)
        self.assertIsNotNone(state.updated_at)
        self.assertIsNone(state.completed_at)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_state(channel)

        other = self.make_channel()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_state(other, operation_id=state.operation_id)

    # テストケース: statusと理由・subject・recovery・完了時刻の不正な組合せを保存する。
    # 期待値: DB制約が矛盾したprojectionをすべて拒否する。
    def test_database_rejects_inconsistent_state_variants(self):
        now = timezone.now()
        cases = (
            {"status": "checking", "safe_reason": "stale_channel"},
            {"status": "checking", "subject_rich_operation_id": uuid4()},
            {"status": "checking", "latest_recovery_operation_id": uuid4()},
            {"status": "checking", "completed_at": now},
            {"status": "unlinking", "subject_rich_operation_id": None},
            {"status": "confirmation_required", "safe_reason": None},
            {"status": "completed", "completed_at": None},
            {"status": "completed", "safe_reason": "stale_channel", "completed_at": now},
        )

        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.make_state(self.make_channel(), **overrides)

    # テストケース: 許可された無効化状態variantを保存する。
    # 期待値: checking、unlinking、確認待ち、completedのclosed setだけが成立する。
    def test_database_accepts_each_valid_state_variant(self):
        now = timezone.now()
        valid = (
            {"status": "checking"},
            {"status": "unlinking", "subject_rich_operation_id": uuid4()},
            {"status": "confirmation_required", "safe_reason": "external_default"},
            {
                "status": "confirmation_required",
                "safe_reason": "recheck_required",
                "subject_rich_operation_id": uuid4(),
                "latest_recovery_operation_id": uuid4(),
            },
            {"status": "completed", "completed_at": now},
        )

        for values in valid:
            with self.subTest(values=values):
                self.make_state(self.make_channel(), **values)

    # テストケース: 既存チャネルを作成し、無効化状態を後から追加・削除する。
    # 期待値: backfillなしで導入でき、物理チャネル削除時はprojectionもCASCADEで消える。
    def test_state_has_no_backfill_and_cascades_with_channel_delete(self):
        legacy = self.make_channel()
        self.assertFalse(ChannelDeactivationState.objects.filter(pk=legacy.pk).exists())

        state = self.make_state(legacy)
        state_id = state.pk
        legacy.delete()

        self.assertFalse(ChannelDeactivationState.objects.filter(pk=state_id).exists())

    # テストケース: 無効化projectionのschemaと表示を検査する。
    # 期待値: 秘密・URL・画像・LINE資源IDの列を持たず、安全な相関情報だけを表示する。
    def test_schema_and_display_exclude_forbidden_data(self):
        state = self.make_state(self.make_channel())
        fields = {field.name for field in ChannelDeactivationState._meta.fields}
        forbidden = {"secret", "token", "url", "image", "line_rich_menu_id"}

        self.assertTrue(forbidden.isdisjoint(fields))
        self.assertNotIn("0012345678", str(state))
        self.assertNotIn(str(state.owner_identity_public_id), str(state))
        self.assertIn(str(state.operation_id), str(state))
        self.assertEqual(
            ChannelDeactivationState._meta.get_field("provider_id").max_length,
            64,
        )
        self.assertLess(
            state.expected_channel_revision - timedelta(microseconds=1),
            state.expected_channel_revision,
        )
