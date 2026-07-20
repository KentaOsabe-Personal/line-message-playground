from datetime import timedelta
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from lineaccounts.models import (
    DeliveryRecipient,
    LineIdentity,
    OwnerAccount,
    OwnerSession,
)
from linechannels.models import LineChannel


class AccountModelTests(TestCase):
    def create_identity(self, *, provider_id="0012345678", subject=None):
        return LineIdentity.objects.create(
            provider_id=provider_id,
            subject=subject or f"U{uuid4().hex}",
            display_name="Owner",
        )

    def create_channel(self, *, provider_id="0012345678"):
        return LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label="通知チャネル",
            provider_id=provider_id,
            is_active=True,
        )

    # テストケース: owner singleton を4つの正規状態で検証する
    # 期待値: stateごとのidentity・generation・確認時刻の組合せだけが有効になる
    def test_owner_account_accepts_only_consistent_state_combinations(self):
        identity = self.create_identity()
        generation = uuid4()
        confirmed_at = timezone.now()
        valid_states = (
            (OwnerAccount.State.VACANT, None, None, None),
            (OwnerAccount.State.ACTIVE, identity, None, None),
            (
                OwnerAccount.State.DEAUTHORIZATION_PENDING,
                identity,
                generation,
                None,
            ),
            (
                OwnerAccount.State.LOCAL_DELETION_PENDING,
                identity,
                generation,
                confirmed_at,
            ),
        )

        for state, owner_identity, unlink_generation, line_deauthorized_at in valid_states:
            with self.subTest(state=state):
                owner = OwnerAccount.objects.get(slot=1)
                owner.state = state
                owner.identity = owner_identity
                owner.unlink_generation = unlink_generation
                owner.line_deauthorized_at = line_deauthorized_at
                owner.full_clean()

        invalid = OwnerAccount(
            slot=1,
            state=OwnerAccount.State.ACTIVE,
            identity=identity,
            unlink_generation=generation,
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()

    # テストケース: singleton slot以外のownerを検証する
    # 期待値: slot=1以外はmodel validationで拒否される
    def test_owner_account_rejects_non_singleton_slot(self):
        with self.assertRaises(ValidationError):
            OwnerAccount(slot=2).full_clean()

    # テストケース: 同一provider・subjectと異providerの同一subjectを登録する
    # 期待値: 同一provider内だけが重複拒否され、異providerは別identityになる
    def test_identity_is_unique_within_provider(self):
        subject = f"U{uuid4().hex}"
        self.create_identity(provider_id="001", subject=subject)

        duplicate = LineIdentity(
            provider_id="001", subject=subject, display_name="Duplicate"
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

        self.create_identity(provider_id="002", subject=subject)
        self.assertEqual(LineIdentity.objects.count(), 2)

    # テストケース: 同じidentity・channelの配信先を重複登録する
    # 期待値: model validationとDB制約の両方で重複が拒否される
    def test_recipient_is_unique_for_identity_and_channel(self):
        identity = self.create_identity()
        channel = self.create_channel()
        DeliveryRecipient.objects.create(identity=identity, line_channel=channel)

        duplicate = DeliveryRecipient(identity=identity, line_channel=channel)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save(force_insert=True)

    # テストケース: ownerに複数端末sessionを作成する
    # 期待値: 端末ごとのopaque UUIDと期限を持つsessionが共存する
    def test_owner_sessions_are_device_specific(self):
        identity = self.create_identity()
        owner = OwnerAccount.objects.get(slot=1)
        owner.state = OwnerAccount.State.ACTIVE
        owner.identity = identity
        owner.save(update_fields=("state", "identity", "updated_at"))

        first = OwnerSession.objects.create(
            owner=owner, expires_at=timezone.now() + timedelta(hours=8)
        )
        second = OwnerSession.objects.create(
            owner=owner, expires_at=timezone.now() + timedelta(hours=8)
        )

        self.assertNotEqual(first.public_id, second.public_id)
        self.assertEqual(owner.sessions.count(), 2)

    # テストケース: 未定義の友だち状態を配信先へ設定する
    # 期待値: model validationで拒否される
    def test_recipient_rejects_unknown_friendship_choice(self):
        recipient = DeliveryRecipient(
            identity=self.create_identity(),
            line_channel=self.create_channel(),
            friendship_state="invalid",
        )

        with self.assertRaises(ValidationError):
            recipient.full_clean()

    # テストケース: account migration適用済みDBを確認する
    # 期待値: vacantなowner singletonが1行だけseedされ、配信監査は独立している
    def test_migration_seeds_singleton_without_delivery_audit_relation(self):
        owner = OwnerAccount.objects.get()

        self.assertEqual(owner.slot, 1)
        self.assertEqual(owner.state, OwnerAccount.State.VACANT)
        self.assertEqual(OwnerAccount.objects.count(), 1)
        from delivery.models import DeliveryAttempt

        self.assertNotIn(
            "identity",
            {field.name for field in DeliveryAttempt._meta.get_fields()},
        )
