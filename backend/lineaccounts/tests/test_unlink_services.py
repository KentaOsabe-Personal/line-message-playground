from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from lineaccounts.authentication import OwnerPrincipal
from lineaccounts.gateway import (
    DeauthorizeSucceeded,
    DeauthorizeUncertain,
    InvalidLineProof,
    VerifyUserTokenSucceeded,
    VerifiedLineIdentity,
)
from delivery.models import DeliveryAttempt
from lineaccounts.models import DeliveryRecipient, OwnerAccount, OwnerSession
from lineaccounts.repositories import (
    AccountPersistenceError,
    DjangoAccountRepository,
    NewRecipient,
)
from lineaccounts.types import LineSubject, UserAccessToken
from lineaccounts.unlink_services import (
    DefaultAccountUnlinkService,
    UnlinkCompleted,
    UnlinkPendingLocalRetry,
    UnlinkPendingReauthentication,
    UnlinkRejected,
)
from linechannels.models import LineChannel
from linechannels.repositories import DjangoLineChannelDirectory


class _Gateway:
    def __init__(self, verification=None, deauthorization=None):
        self.verification = verification
        self.deauthorization = deauthorization or DeauthorizeSucceeded()
        self.verify_calls = 0
        self.deauthorize_calls = 0

    def verify_user_access_token(self, token, expected_subject):
        self.verify_calls += 1
        return self.verification or VerifyUserTokenSucceeded(expected_subject)

    def deauthorize(self, token):
        self.deauthorize_calls += 1
        return self.deauthorization


class _Lock:
    def __init__(self, acquired=True):
        self.acquired = acquired

    def acquire(self, owner_slot):
        acquired = self.acquired

        class Context:
            def __enter__(self):
                return acquired

            def __exit__(self, *args):
                return False

        return Context()


class _OnAcquireLock(_Lock):
    def __init__(self, callback):
        super().__init__(True)
        self.callback = callback

    def acquire(self, owner_slot):
        callback = self.callback

        class Context:
            def __enter__(self):
                callback()
                return True

            def __exit__(self, *args):
                return False

        return Context()


class AccountUnlinkServiceTests(TestCase):
    def setUp(self):
        self.repository = DjangoAccountRepository()
        self.subject = LineSubject(f"U{uuid4().hex}")
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            identity = self.repository.upsert_identity(
                VerifiedLineIdentity("0012345678", self.subject, "Owner")
            )
            owner = self.repository.bind_owner_identity(owner, identity.public_id)
            session = self.repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )
        self.identity = identity
        self.principal = OwnerPrincipal(session.public_id, identity.public_id, "active")
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id="1234567890",
            bot_user_id=f"U{uuid4().hex}",
            label="通知チャネル",
            provider_id="0012345678",
            is_active=True,
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.repository.create_recipient(
                owner,
                NewRecipient(identity.public_id, self.channel.public_id, "friend"),
            )

    def service(self, gateway=None, lock=None):
        return DefaultAccountUnlinkService(
            gateway or _Gateway(),
            self.repository,
            lock or _Lock(),
            DjangoLineChannelDirectory(),
        )

    # テストケース: active ownerがunlink previewを取得する
    # 期待値: 表示名・channel label・件数・監査保持・5分署名tokenだけを返す
    def test_preview_returns_safe_snapshot_summary(self):
        preview = self.service().preview(self.principal, timezone.now())

        self.assertEqual(preview.display_name, "Owner")
        self.assertEqual(preview.recipient_count, 1)
        self.assertEqual(preview.channel_labels, ("通知チャネル",))
        self.assertTrue(preview.delivery_audit_retained)
        self.assertNotIn(self.subject.reveal_for_identity_binding(), repr(preview))

    # テストケース: fresh本人証明とconfirmationで全連携解除を実行する
    # 期待値: fence、single-flight LINE 取消、marker、原子的削除を順に完了する
    def test_execute_completes_saga_and_deletes_all_personal_data(self):
        gateway = _Gateway()
        service = self.service(gateway)
        now = timezone.now()
        preview = service.preview(self.principal, now)
        audit = DeliveryAttempt.objects.create(
            operation_id=uuid4(),
            subject="監査対象",
            body="本文",
            formatted_text="整形済み",
            content_fingerprint="a" * 64,
            active_content_fingerprint="a" * 64,
            accepted_at=now,
            processing_expires_at=now + timedelta(minutes=1),
        )

        result = service.execute(
            self.principal,
            preview.confirmation_token,
            UserAccessToken("fresh-token"),
            now,
        )

        self.assertIsInstance(result, UnlinkCompleted)
        self.assertEqual(gateway.verify_calls, 1)
        self.assertEqual(gateway.deauthorize_calls, 1)
        self.assertEqual(OwnerAccount.objects.get(slot=1).state, "vacant")
        self.assertEqual(OwnerSession.objects.count(), 0)
        self.assertEqual(DeliveryRecipient.objects.count(), 0)
        audit.refresh_from_db()
        self.assertEqual(audit.subject, "監査対象")

    # テストケース: 無効proofまたはstale confirmationで初回解除を要求する
    # 期待値: LINE取消とfenceを開始せず安全な拒否へ収束する
    def test_invalid_proof_and_stale_confirmation_do_not_start_fence(self):
        now = timezone.now()
        invalid_gateway = _Gateway(verification=InvalidLineProof())
        service = self.service(invalid_gateway)
        preview = service.preview(self.principal, now)
        invalid = service.execute(
            self.principal,
            preview.confirmation_token,
            UserAccessToken("invalid"),
            now,
        )
        stale = self.service().execute(
            self.principal,
            f"{preview.confirmation_token}x",
            UserAccessToken("fresh"),
            now,
        )

        self.assertEqual(invalid, UnlinkRejected("invalid_line_proof"))
        self.assertEqual(stale, UnlinkRejected("stale_confirmation"))
        self.assertEqual(OwnerAccount.objects.get(slot=1).state, "active")
        self.assertEqual(invalid_gateway.deauthorize_calls, 0)

    # テストケース: preview後にrecipient snapshotを変更して旧tokenを実行する
    # 期待値: remote本人確認後の再計算でstaleとなりfenceとLINE取消を開始しない
    def test_snapshot_change_rejects_confirmation_before_fence(self):
        now = timezone.now()
        gateway = _Gateway()
        service = self.service(gateway)
        preview = service.preview(self.principal, now)
        second_channel = LineChannel.objects.create(
            messaging_api_channel_id="9876543210",
            bot_user_id=f"U{uuid4().hex}",
            label="追加チャネル",
            provider_id="0012345678",
            is_active=True,
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.repository.create_recipient(
                owner,
                NewRecipient(
                    self.identity.public_id, second_channel.public_id, "unknown"
                ),
            )

        result = service.execute(
            self.principal,
            preview.confirmation_token,
            UserAccessToken("fresh"),
            now,
        )

        self.assertEqual(result, UnlinkRejected("stale_confirmation"))
        self.assertEqual(OwnerAccount.objects.get(slot=1).state, "active")
        self.assertEqual(gateway.deauthorize_calls, 0)

    # テストケース: LINE結果不確定またはadvisory lock競合になる
    # 期待値: identityを保持してfresh再認証pendingまたは競合を返す
    def test_uncertain_and_busy_keep_deauthorization_pending(self):
        now = timezone.now()
        uncertain_gateway = _Gateway(deauthorization=DeauthorizeUncertain())
        service = self.service(uncertain_gateway)
        preview = service.preview(self.principal, now)
        uncertain = service.execute(
            self.principal,
            preview.confirmation_token,
            UserAccessToken("fresh"),
            now,
        )

        self.assertIsInstance(uncertain, UnlinkPendingReauthentication)
        self.assertEqual(OwnerAccount.objects.get(slot=1).state, "deauthorization_pending")
        pending_principal = OwnerPrincipal(
            self.principal.owner_session_id,
            self.principal.identity_public_id,
            "deauthorization_pending",
        )
        busy_gateway = _Gateway()
        busy = self.service(gateway=busy_gateway, lock=_Lock(False)).execute(
            pending_principal, None, UserAccessToken("fresh"), now
        )
        self.assertEqual(busy, UnlinkRejected("unlink_in_progress"))
        self.assertEqual(busy_gateway.deauthorize_calls, 0)

        resumed_gateway = _Gateway()
        resumed = self.service(gateway=resumed_gateway).execute(
            pending_principal, None, UserAccessToken("fresh-again"), now
        )
        self.assertIsInstance(resumed, UnlinkCompleted)
        self.assertEqual(resumed_gateway.verify_calls, 1)
        self.assertEqual(resumed_gateway.deauthorize_calls, 1)

    # テストケース: token検証後かつadvisory lock取得前にgenerationが置換される
    # 期待値: lock後のstate再読込で旧attemptを隔離しLINEへ到達しない
    def test_generation_aba_is_rejected_after_execution_lock(self):
        now = timezone.now()
        first_gateway = _Gateway(deauthorization=DeauthorizeUncertain())
        first_service = self.service(first_gateway)
        preview = first_service.preview(self.principal, now)
        first_service.execute(
            self.principal,
            preview.confirmation_token,
            UserAccessToken("fresh"),
            now,
        )
        new_generation = uuid4()

        def replace_generation():
            OwnerAccount.objects.filter(slot=1).update(
                unlink_generation=new_generation,
                updated_at=timezone.now(),
            )

        pending_principal = OwnerPrincipal(
            self.principal.owner_session_id,
            self.principal.identity_public_id,
            "deauthorization_pending",
        )
        delayed_gateway = _Gateway()
        result = self.service(
            gateway=delayed_gateway,
            lock=_OnAcquireLock(replace_generation),
        ).execute(pending_principal, None, UserAccessToken("fresh-again"), now)

        self.assertEqual(result, UnlinkRejected("unlink_attempt_stale"))
        self.assertEqual(delayed_gateway.deauthorize_calls, 0)
        self.assertEqual(
            OwnerAccount.objects.get(slot=1).unlink_generation, new_generation
        )

    # テストケース: LINE 204後のmarker commitだけが失敗する
    # 期待値: identityとrecipientを保持してfresh再認証pendingへ戻す
    def test_marker_failure_keeps_identity_and_reauthentication_stage(self):
        now = timezone.now()
        gateway = _Gateway()
        service = self.service(gateway)
        preview = service.preview(self.principal, now)

        with patch.object(
            self.repository,
            "mark_line_deauthorized",
            side_effect=AccountPersistenceError("storage_unavailable"),
        ):
            result = service.execute(
                self.principal,
                preview.confirmation_token,
                UserAccessToken("fresh"),
                now,
            )

        self.assertIsInstance(result, UnlinkPendingReauthentication)
        self.assertEqual(OwnerAccount.objects.get(slot=1).state, "deauthorization_pending")
        self.assertEqual(DeliveryRecipient.objects.count(), 1)

    # テストケース: marker保存後のlocal削除が一時失敗して再開する
    # 期待値: LINEを再呼出しせずlocal-only retryで完了する
    def test_local_pending_retries_without_line_call(self):
        now = timezone.now()
        gateway = _Gateway()
        service = self.service(gateway)
        preview = service.preview(self.principal, now)
        with patch.object(
            self.repository,
            "finalize_unlink",
            side_effect=AccountPersistenceError("storage_unavailable"),
        ):
            first = service.execute(
                self.principal,
                preview.confirmation_token,
                UserAccessToken("fresh"),
                now,
            )
        self.assertIsInstance(first, UnlinkPendingLocalRetry)

        pending_principal = OwnerPrincipal(
            self.principal.owner_session_id,
            self.principal.identity_public_id,
            "local_deletion_pending",
        )
        resumed = service.execute(pending_principal, None, None, now)

        self.assertIsInstance(resumed, UnlinkCompleted)
        self.assertEqual(gateway.deauthorize_calls, 1)

    # テストケース: 全解除完了後に同じ本人を再linkして旧confirmationをreplayする
    # 期待値: 新identity UUIDへ旧principal/tokenを適用せず新ownerを維持する
    def test_relink_rejects_old_principal_and_confirmation_replay(self):
        now = timezone.now()
        service = self.service()
        preview = service.preview(self.principal, now)
        completed = service.execute(
            self.principal,
            preview.confirmation_token,
            UserAccessToken("fresh"),
            now,
        )
        self.assertIsInstance(completed, UnlinkCompleted)
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            new_identity = self.repository.upsert_identity(
                VerifiedLineIdentity("0012345678", self.subject, "Owner")
            )
            self.repository.bind_owner_identity(owner, new_identity.public_id)

        replay = service.execute(
            self.principal,
            preview.confirmation_token,
            UserAccessToken("old-token"),
            now,
        )

        self.assertEqual(replay, UnlinkRejected("owner_not_allowed"))
        self.assertEqual(
            OwnerAccount.objects.get(slot=1).identity.public_id,
            new_identity.public_id,
        )

    # テストケース: 各owner stageへ許可されないcredential組合せを渡す
    # 期待値: fieldを黙って無視せずvalidation errorで拒否する
    def test_rejects_request_fields_not_allowed_for_current_stage(self):
        now = timezone.now()
        service = self.service(_Gateway(deauthorization=DeauthorizeUncertain()))
        preview = service.preview(self.principal, now)
        service.execute(
            self.principal,
            preview.confirmation_token,
            UserAccessToken("fresh"),
            now,
        )
        pending_principal = OwnerPrincipal(
            self.principal.owner_session_id,
            self.principal.identity_public_id,
            "deauthorization_pending",
        )

        pending_extra = service.execute(
            pending_principal,
            preview.confirmation_token,
            UserAccessToken("fresh-again"),
            now,
        )

        self.assertEqual(pending_extra, UnlinkRejected("validation_error"))

    # テストケース: pending unlinkの内部指標を取得する
    # 期待値: stage別件数と経過秒だけを返し個人識別子を含まない
    def test_reports_safe_pending_metrics(self):
        now = timezone.now()
        service = self.service(_Gateway(deauthorization=DeauthorizeUncertain()))
        preview = service.preview(self.principal, now)
        service.execute(
            self.principal,
            preview.confirmation_token,
            UserAccessToken("fresh"),
            now,
        )

        metrics = service.pending_metrics(now + timedelta(seconds=30))

        self.assertEqual(metrics.deauthorization_pending_count, 1)
        self.assertGreaterEqual(metrics.oldest_deauthorization_pending_seconds, 29)
        self.assertNotIn("Owner", repr(metrics))
        self.assertNotIn(str(self.identity.public_id), repr(metrics))
