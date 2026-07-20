from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from lineaccounts.gateway import (
    InvalidLineProof,
    LinePlatformUnavailable,
    VerifiedLineIdentity,
    VerifyIdentitySucceeded,
)
from lineaccounts.models import (
    DeliveryRecipient,
    LineIdentity,
    OwnerAccount,
    OwnerSession,
)
from lineaccounts.repositories import DjangoAccountRepository, NewRecipient
from lineaccounts.runtime import (
    OwnerEligibilityDigest,
    OwnerEligibilityUnavailable,
    derive_owner_digest,
)
from lineaccounts.session_services import (
    DefaultAccountSessionService,
    DefaultOwnerIdentityBinder,
    AnonymousSessionStatus,
    AuthenticatedSessionStatus,
    EstablishSessionRejected,
    EstablishSessionSucceeded,
    OwnerBindingRejected,
    OwnerBindingSucceeded,
    UnlinkingSessionStatus,
)
from lineaccounts.types import IdToken, LineSubject
from linechannels.models import LineChannel


class OwnerIdentityBinderTests(TestCase):
    def setUp(self):
        self.provider_id = "0012345678"
        self.subject = f"U{uuid4().hex}"
        self.identity = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(self.subject),
            display_name="Owner",
        )
        self.repository = DjangoAccountRepository()

    def binder(self, eligibility=None):
        return DefaultOwnerIdentityBinder(
            self.repository,
            eligibility
            or OwnerEligibilityDigest(
                derive_owner_digest(self.provider_id, self.subject)
            ),
        )

    # テストケース: 事前許可digestと一致する検証済みidentityをvacant ownerへbindingする
    # 期待値: ownerと最小identityがactive状態として同一transactionで保存される
    def test_binds_only_preapproved_identity_to_vacant_owner(self):
        result = self.binder().bind(self.identity)

        self.assertIsInstance(result, OwnerBindingSucceeded)
        owner = OwnerAccount.objects.select_related("identity").get(slot=1)
        self.assertEqual(owner.state, OwnerAccount.State.ACTIVE)
        self.assertEqual(owner.identity.provider_id, self.provider_id)
        self.assertEqual(owner.identity.subject, self.subject)
        self.assertEqual(owner.identity.display_name, "Owner")

    # テストケース: owner適格条件が未設定またはdigest不一致のidentityをbindingする
    # 期待値: どちらも同じ安全な拒否となりownerとidentityを作成しない
    def test_unavailable_and_mismatch_share_safe_rejection_without_mutation(self):
        mismatch = OwnerEligibilityDigest("0" * 64)

        for eligibility in (OwnerEligibilityUnavailable(), mismatch):
            with self.subTest(eligibility=type(eligibility).__name__):
                result = self.binder(eligibility).bind(self.identity)
                self.assertEqual(result, OwnerBindingRejected())
                self.assertEqual(OwnerAccount.objects.get(slot=1).state, "vacant")
                self.assertEqual(LineIdentity.objects.count(), 0)

    # テストケース: active ownerを同一identityで再認証し表示名を更新する
    # 期待値: owner bindingを維持しidentityを重複せず最新表示名へ更新する
    def test_reauthentication_updates_display_name_for_same_identity(self):
        first = self.binder().bind(self.identity)
        updated = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(self.subject),
            display_name="Updated Owner",
        )

        second = self.binder().bind(updated)

        self.assertIsInstance(first, OwnerBindingSucceeded)
        self.assertIsInstance(second, OwnerBindingSucceeded)
        self.assertEqual(LineIdentity.objects.count(), 1)
        self.assertEqual(LineIdentity.objects.get().display_name, "Updated Owner")

    # テストケース: active ownerへ事前許可digest自体は一致する別identityをbindingする
    # 期待値: owner_not_allowedへ収束し既存bindingと既存identityだけを維持する
    def test_rejects_different_identity_for_active_owner_without_orphan(self):
        initial = self.binder().bind(self.identity)
        other_subject = f"U{uuid4().hex}"
        other = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(other_subject),
            display_name="Other",
        )
        other_binder = self.binder(
            OwnerEligibilityDigest(
                derive_owner_digest(self.provider_id, other_subject)
            )
        )

        result = other_binder.bind(other)

        self.assertIsInstance(initial, OwnerBindingSucceeded)
        self.assertEqual(result, OwnerBindingRejected())
        owner = OwnerAccount.objects.select_related("identity").get(slot=1)
        self.assertEqual(owner.identity.subject, self.subject)
        self.assertEqual(LineIdentity.objects.count(), 1)


class _GatewayStub:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def verify_id_token(self, token):
        self.calls += 1
        return self.result


class AccountSessionServiceTests(TestCase):
    def setUp(self):
        self.provider_id = "0012345678"
        self.subject = f"U{uuid4().hex}"
        self.identity = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(self.subject),
            display_name="Owner",
        )
        self.now = timezone.now()
        self.repository = DjangoAccountRepository()
        self.eligibility = OwnerEligibilityDigest(
            derive_owner_digest(self.provider_id, self.subject)
        )

    def service(self, result=None):
        gateway = _GatewayStub(result or VerifyIdentitySucceeded(self.identity))
        return DefaultAccountSessionService(
            gateway, self.repository, self.eligibility
        ), gateway

    def create_recipient(self):
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label="通知チャネル",
            provider_id=self.provider_id,
            is_active=True,
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            return self.repository.create_recipient(
                owner,
                NewRecipient(
                    identity_id=owner.identity_id,
                    channel_id=channel.public_id,
                    friendship_state=DeliveryRecipient.FriendshipState.FRIEND,
                ),
            )

    # テストケース: 同じownerが2端末から有効な本人証明で認証する
    # 期待値: identityを共有する異なるopaque session ledgerが8時間期限で併存する
    def test_establishes_independent_sessions_for_multiple_devices(self):
        service, gateway = self.service()

        first = service.establish(IdToken("proof-one"), self.now)
        second = service.establish(IdToken("proof-two"), self.now)

        self.assertIsInstance(first, EstablishSessionSucceeded)
        self.assertIsInstance(second, EstablishSessionSucceeded)
        self.assertNotEqual(first.session.public_id, second.session.public_id)
        self.assertEqual(first.session.expires_at, self.now + timedelta(hours=8))
        self.assertEqual(OwnerSession.objects.count(), 2)
        self.assertEqual(LineIdentity.objects.count(), 1)
        self.assertEqual(gateway.calls, 2)

    # テストケース: active ownerの端末session状態を代表データ量で取得する。
    # 期待値: identityを含む固定1 queryで解決しrecipientやcredential tableを参照しない。
    def test_active_session_status_uses_one_safe_query(self):
        service, _ = self.service()
        established = service.establish(IdToken("proof"), self.now)
        for _ in range(8):
            self.create_recipient()

        with self.assertNumQueries(1) as captured:
            status = service.get_status(established.session.public_id, self.now)

        self.assertIsInstance(status, AuthenticatedSessionStatus)
        sql = "\n".join(query["sql"].lower() for query in captured.captured_queries)
        self.assertNotIn("linechannels_linechannelcredential", sql)
        self.assertNotIn("lineaccounts_deliveryrecipient", sql)

    # テストケース: active ownerが新しい検証済み表示名で通常再認証する
    # 期待値: bindingを維持し表示名を更新して新しい端末sessionを返す
    def test_reauthentication_updates_profile_and_creates_new_session(self):
        service, _ = self.service()
        first = service.establish(IdToken("proof-one"), self.now)
        updated = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(self.subject),
            display_name="Updated Owner",
        )
        service, _ = self.service(VerifyIdentitySucceeded(updated))

        second = service.establish(IdToken("proof-two"), self.now)

        self.assertIsInstance(first, EstablishSessionSucceeded)
        self.assertIsInstance(second, EstablishSessionSucceeded)
        self.assertEqual(second.display_name, "Updated Owner")
        self.assertEqual(LineIdentity.objects.get().display_name, "Updated Owner")
        self.assertEqual(OwnerSession.objects.count(), 2)

    # テストケース: unlink pending中の同一ownerがfresh本人証明で再認証する
    # 期待値: resume用sessionを追加しowner状態をactiveへ戻さずunlinkingを返す
    def test_pending_reauthentication_keeps_unlink_fence(self):
        service, _ = self.service()
        active = service.establish(IdToken("proof-one"), self.now)
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.repository.begin_unlink(owner, uuid4())

        pending = service.establish(IdToken("proof-two"), self.now)

        self.assertIsInstance(active, EstablishSessionSucceeded)
        self.assertIsInstance(pending, EstablishSessionSucceeded)
        self.assertEqual(pending.state, "unlinking")
        self.assertEqual(
            OwnerAccount.objects.get(slot=1).state,
            OwnerAccount.State.DEAUTHORIZATION_PENDING,
        )
        self.assertEqual(OwnerSession.objects.count(), 2)

        status = service.get_status(pending.session.public_id, self.now)
        self.assertIsInstance(status, UnlinkingSessionStatus)
        self.assertEqual(status.stage, "deauthorization_pending")
        self.assertEqual(status.retry_action, "reauthenticate")

    # テストケース: 現在端末をlogoutする
    # 期待値: 指定ledgerだけを削除し他端末session・identity・owner bindingを維持する
    def test_logout_deletes_only_current_device_session(self):
        service, _ = self.service()
        first = service.establish(IdToken("proof-one"), self.now)
        second = service.establish(IdToken("proof-two"), self.now)
        recipient = self.create_recipient()

        result = service.logout(first.session.public_id)

        self.assertTrue(result.deleted)
        self.assertFalse(
            OwnerSession.objects.filter(public_id=first.session.public_id).exists()
        )
        self.assertTrue(
            OwnerSession.objects.filter(public_id=second.session.public_id).exists()
        )
        self.assertEqual(LineIdentity.objects.count(), 1)
        self.assertTrue(
            DeliveryRecipient.objects.filter(public_id=recipient.public_id).exists()
        )

    # テストケース: 1端末のsession期限後にstatusを確認する
    # 期待値: 期限切れledgerだけを匿名化・削除し、他端末・identity・recipientを維持する
    def test_expiry_anonymizes_only_expired_device_and_preserves_account_data(self):
        service, _ = self.service()
        first = service.establish(IdToken("proof-one"), self.now)
        second_now = self.now + timedelta(hours=1)
        second = service.establish(IdToken("proof-two"), second_now)
        recipient = self.create_recipient()
        after_first_expiry = self.now + timedelta(hours=8, seconds=1)

        expired_status = service.get_status(
            first.session.public_id, after_first_expiry
        )
        active_status = service.get_status(
            second.session.public_id, after_first_expiry
        )

        self.assertEqual(expired_status, AnonymousSessionStatus())
        self.assertIsInstance(active_status, AuthenticatedSessionStatus)
        self.assertEqual(active_status.display_name, "Owner")
        self.assertFalse(
            OwnerSession.objects.filter(public_id=first.session.public_id).exists()
        )
        self.assertTrue(
            OwnerSession.objects.filter(public_id=second.session.public_id).exists()
        )
        self.assertEqual(LineIdentity.objects.count(), 1)
        self.assertTrue(
            DeliveryRecipient.objects.filter(public_id=recipient.public_id).exists()
        )

    # テストケース: 無効な本人証明またはLINE一時障害でsession確立を要求する
    # 期待値: 安全な分類を返しowner・identity・sessionを一切作成しない
    def test_rejects_invalid_or_unavailable_proof_without_mutation(self):
        cases = (
            (InvalidLineProof(), "invalid_line_proof"),
            (LinePlatformUnavailable(), "line_unavailable"),
        )
        for gateway_result, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                service, _ = self.service(gateway_result)
                result = service.establish(IdToken("bad-proof"), self.now)
                self.assertEqual(result, EstablishSessionRejected(expected_code))
                self.assertEqual(OwnerAccount.objects.get(slot=1).state, "vacant")
                self.assertEqual(LineIdentity.objects.count(), 0)
                self.assertEqual(OwnerSession.objects.count(), 0)
