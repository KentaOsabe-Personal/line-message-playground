from uuid import uuid4

from django.db import transaction
from django.test import TransactionTestCase

from lineaccounts.friendship_repositories import (
    DjangoAccountProjectionRepository,
)
from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerAccount
from lineaccounts.types import LineSubject
from linechannels.models import LineChannel
from linefriendships.types import LockedRecipientProjection, ProjectionTargetMissing


class AccountProjectionRepositoryTests(TransactionTestCase):
    def setUp(self):
        self.repository = DjangoAccountProjectionRepository()
        self.provider_id = "0012345678"
        self.subject_value = "U" + "a" * 32
        self.identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=self.subject_value,
            display_name="Owner",
        )
        OwnerAccount.objects.get_or_create(slot=1)
        OwnerAccount.objects.filter(slot=1).update(
            state=OwnerAccount.State.ACTIVE,
            identity=self.identity,
        )
        self.channel = self.create_channel(provider_id=self.provider_id)
        self.recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.channel,
            enabled=False,
            friendship_state=DeliveryRecipient.FriendshipState.UNKNOWN,
        )

    def create_channel(self, *, provider_id):
        return LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id="U" + uuid4().hex,
            label="通知チャネル",
            provider_id=provider_id,
            is_active=True,
        )

    # テストケース: provider・subject・channelが一致する既存recipientをlockする
    # 期待値: 登録境界と現在の状態/orderをimmutable snapshotで返す
    def test_locks_only_exact_existing_recipient(self):
        with transaction.atomic():
            target = self.repository.lock_target(
                channel_public_id=self.channel.public_id,
                provider_id=self.provider_id,
                subject=LineSubject(self.subject_value),
            )

        self.assertIsInstance(target, LockedRecipientProjection)
        assert isinstance(target, LockedRecipientProjection)
        self.assertEqual(target.recipient_public_id, self.recipient.public_id)
        self.assertEqual(target.friendship_state, "unknown")
        self.assertEqual(target.registered_at, self.recipient.created_at)
        self.assertIsNone(target.last_occurred_at_ms)

    # テストケース: lock済みrecipientへ新しい友だちprojectionを適用する
    # 期待値: state・order・updated_atだけを更新しenabledと登録時刻を維持する
    def test_applies_only_owned_projection_fields(self):
        original_registered_at = self.recipient.created_at
        original_updated_at = self.recipient.updated_at

        with transaction.atomic():
            target = self.repository.lock_target(
                channel_public_id=self.channel.public_id,
                provider_id=self.provider_id,
                subject=LineSubject(self.subject_value),
            )
            assert isinstance(target, LockedRecipientProjection)
            self.repository.apply_locked(
                target,
                friendship_state="friend",
                occurred_at_ms=123,
                webhook_event_id="01J00000000000000000000000",
            )

        stored = DeliveryRecipient.objects.get(pk=self.recipient.pk)
        self.assertEqual(stored.friendship_state, "friend")
        self.assertEqual(stored.last_friendship_event_occurred_at_ms, 123)
        self.assertEqual(
            stored.last_friendship_webhook_event_id,
            "01J00000000000000000000000",
        )
        self.assertFalse(stored.enabled)
        self.assertEqual(stored.created_at, original_registered_at)
        self.assertGreater(stored.updated_at, original_updated_at)

    # テストケース: exact targetと同時に他provider・他channel・他identityのrecipientを保持する
    # 期待値: target更新後も3種類の非対象recipientの全projection fieldが不変となる
    def test_apply_keeps_every_non_target_recipient_unchanged(self):
        same_provider_other_channel = self.create_channel(
            provider_id=self.provider_id
        )
        other_provider = "0099999999"
        other_provider_channel = self.create_channel(provider_id=other_provider)
        same_provider_other_identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject="U" + "b" * 32,
            display_name="Other identity",
        )
        other_provider_identity = LineIdentity.objects.create(
            provider_id=other_provider,
            subject=self.subject_value,
            display_name="Other provider",
        )
        non_targets = (
            DeliveryRecipient.objects.create(
                identity=self.identity,
                line_channel=same_provider_other_channel,
                enabled=True,
                friendship_state="not_friend",
            ),
            DeliveryRecipient.objects.create(
                identity=same_provider_other_identity,
                line_channel=self.channel,
                enabled=False,
                friendship_state="friend",
            ),
            DeliveryRecipient.objects.create(
                identity=other_provider_identity,
                line_channel=other_provider_channel,
                enabled=False,
                friendship_state="not_friend",
            ),
        )
        before = {
            recipient.public_id: (
                recipient.friendship_state,
                recipient.last_friendship_event_occurred_at_ms,
                recipient.last_friendship_webhook_event_id,
                recipient.enabled,
                recipient.created_at,
                recipient.updated_at,
            )
            for recipient in non_targets
        }

        with transaction.atomic():
            target = self.repository.lock_target(
                channel_public_id=self.channel.public_id,
                provider_id=self.provider_id,
                subject=LineSubject(self.subject_value),
            )
            assert isinstance(target, LockedRecipientProjection)
            self.repository.apply_locked(
                target,
                friendship_state="friend",
                occurred_at_ms=456,
                webhook_event_id="01J00000000000000000000001",
            )

        for recipient in DeliveryRecipient.objects.filter(
            public_id__in=before
        ):
            self.assertEqual(
                (
                    recipient.friendship_state,
                    recipient.last_friendship_event_occurred_at_ms,
                    recipient.last_friendship_webhook_event_id,
                    recipient.enabled,
                    recipient.created_at,
                    recipient.updated_at,
                ),
                before[recipient.public_id],
            )

    # テストケース: provider・subject・channelのいずれかが一致しないtargetを探す
    # 期待値: 存在詳細を区別せずmissingへ縮約し、他のrecipientを変更しない
    def test_collapses_all_non_exact_matches_to_missing(self):
        other_provider = "0099999999"
        other_channel = self.create_channel(provider_id=self.provider_id)
        candidates = (
            {
                "channel_public_id": self.channel.public_id,
                "provider_id": other_provider,
                "subject": LineSubject(self.subject_value),
            },
            {
                "channel_public_id": self.channel.public_id,
                "provider_id": self.provider_id,
                "subject": LineSubject("U" + "b" * 32),
            },
            {
                "channel_public_id": other_channel.public_id,
                "provider_id": self.provider_id,
                "subject": LineSubject(self.subject_value),
            },
        )

        for candidate in candidates:
            with self.subTest(candidate=str(candidate["channel_public_id"])):
                with transaction.atomic():
                    result = self.repository.lock_target(**candidate)
                self.assertEqual(result, ProjectionTargetMissing())

        self.assertEqual(DeliveryRecipient.objects.count(), 1)

    # テストケース: unlink中またはrecipient削除後のtargetを探す
    # 期待値: 行を再作成せずmissingへ縮約する
    def test_does_not_recreate_unlinked_targets(self):
        OwnerAccount.objects.filter(slot=1).update(
            state=OwnerAccount.State.DEAUTHORIZATION_PENDING,
            unlink_generation=uuid4(),
        )
        with transaction.atomic():
            pending = self.repository.lock_target(
                channel_public_id=self.channel.public_id,
                provider_id=self.provider_id,
                subject=LineSubject(self.subject_value),
            )
        self.assertEqual(pending, ProjectionTargetMissing())

        OwnerAccount.objects.filter(slot=1).update(
            state=OwnerAccount.State.ACTIVE,
            unlink_generation=None,
        )
        self.recipient.delete()
        with transaction.atomic():
            deleted = self.repository.lock_target(
                channel_public_id=self.channel.public_id,
                provider_id=self.provider_id,
                subject=LineSubject(self.subject_value),
            )
        self.assertEqual(deleted, ProjectionTargetMissing())
        self.assertEqual(DeliveryRecipient.objects.count(), 0)

    # テストケース: transaction外でlocking readを呼び出す
    # 期待値: programming errorとして拒否し通常readへ劣化しない
    def test_requires_active_transaction(self):
        with self.assertRaises(RuntimeError):
            self.repository.lock_target(
                channel_public_id=self.channel.public_id,
                provider_id=self.provider_id,
                subject=LineSubject(self.subject_value),
            )
