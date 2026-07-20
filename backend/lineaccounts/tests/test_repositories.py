import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock
from uuid import uuid4

from django.db import DatabaseError, OperationalError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from delivery.models import DeliveryAttempt
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import (
    DeliveryRecipient,
    LineIdentity,
    OwnerAccount,
    OwnerSession,
)
from lineaccounts.repositories import (
    AccountPersistenceError,
    AccountRepositoryProgrammingError,
    AccountStateError,
    DjangoAccountRepository,
    NewRecipient,
)
from lineaccounts.types import LineSubject
from linechannels.models import LineChannel


class AccountRepositoryTests(TestCase):
    def setUp(self):
        self.repository = DjangoAccountRepository()
        self.provider_id = "0012345678"

    def verified_identity(self, *, subject=None, display_name="Owner"):
        return VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(subject or f"U{uuid4().hex}"),
            display_name=display_name,
        )

    def bind_owner(self, *, identity=None):
        proof = identity or self.verified_identity()
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            stored_identity = self.repository.upsert_identity(proof)
            owner = self.repository.bind_owner_identity(owner, stored_identity.public_id)
        return owner, stored_identity

    def create_channel(self, *, provider_id=None, active=True):
        return LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label="通知チャネル",
            provider_id=provider_id or self.provider_id,
            is_active=active,
        )

    def begin_local_deletion(self):
        owner, identity = self.bind_owner()
        generation = uuid4()
        confirmed_at = timezone.now()
        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            pending = self.repository.begin_unlink(locked, generation)
            local_pending = self.repository.mark_line_deauthorized(
                pending, generation, confirmed_at
            )
        return local_pending, identity, generation, confirmed_at

    # テストケース: vacant ownerをlockして検証済みidentityを初回bindingする
    # 期待値: ownerとidentityがactive状態へ同一transactionで永続化される
    def test_locks_owner_and_binds_verified_identity(self):
        proof = self.verified_identity()

        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            identity = self.repository.upsert_identity(proof)
            bound = self.repository.bind_owner_identity(owner, identity.public_id)

        stored_owner = OwnerAccount.objects.select_related("identity").get(slot=1)
        self.assertEqual(bound.state, OwnerAccount.State.ACTIVE)
        self.assertEqual(stored_owner.identity.public_id, identity.public_id)
        self.assertEqual(stored_owner.identity.display_name, "Owner")

    # テストケース: 同一provider・subjectを新しい表示名で再認証する
    # 期待値: identityを重複作成せず表示名だけを検証済み最新値へ更新する
    def test_upsert_identity_updates_display_name_without_duplicate(self):
        subject = f"U{uuid4().hex}"
        self.bind_owner(identity=self.verified_identity(subject=subject, display_name="前"))

        with transaction.atomic():
            identity = self.repository.upsert_identity(
                self.verified_identity(subject=subject, display_name="後")
            )

        self.assertEqual(LineIdentity.objects.count(), 1)
        self.assertEqual(identity.display_name, "後")
        self.assertEqual(LineIdentity.objects.get().display_name, "後")

    # テストケース: active ownerへ異なるidentityをbindingする
    # 期待値: 既存owner bindingを維持してidentity mismatchとして拒否する
    def test_rejects_binding_a_different_identity(self):
        _, existing = self.bind_owner()

        with self.assertRaises(AccountStateError) as raised, transaction.atomic():
            locked = self.repository.lock_owner_account()
            different = self.repository.upsert_identity(self.verified_identity())
            self.repository.bind_owner_identity(locked, different.public_id)

        self.assertEqual(raised.exception.code, "identity_mismatch")
        self.assertEqual(
            OwnerAccount.objects.get(slot=1).identity.public_id,
            existing.public_id,
        )

    # テストケース: 同じownerへ複数の端末sessionを作成する
    # 期待値: opaque IDが異なるsession ledgerが併存する
    def test_creates_device_specific_owner_sessions(self):
        _, identity = self.bind_owner()
        expires_at = timezone.now() + timedelta(hours=8)

        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            first = self.repository.create_owner_session(locked, expires_at)
            second = self.repository.create_owner_session(locked, expires_at)

        self.assertNotEqual(first.public_id, second.public_id)
        self.assertEqual(first.identity_id, identity.public_id)
        self.assertEqual(OwnerSession.objects.count(), 2)

    # テストケース: 期限切れsessionを検索する
    # 期待値: 期限切れledgerだけを削除し他端末sessionを維持する
    def test_get_session_lazily_deletes_only_expired_ledger(self):
        self.bind_owner()
        now = timezone.now()
        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            expired = self.repository.create_owner_session(
                locked, now - timedelta(seconds=1)
            )
            active = self.repository.create_owner_session(
                locked, now + timedelta(hours=1)
            )

        self.assertIsNone(self.repository.get_session(expired.public_id, now))
        self.assertEqual(self.repository.get_session(active.public_id, now), active)
        self.assertFalse(OwnerSession.objects.filter(public_id=expired.public_id).exists())
        self.assertTrue(OwnerSession.objects.filter(public_id=active.public_id).exists())

    # テストケース: 現在端末のsessionだけを削除する
    # 期待値: 他端末sessionとidentityを維持する
    def test_delete_owner_session_affects_only_selected_device(self):
        self.bind_owner()
        expires_at = timezone.now() + timedelta(hours=8)
        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            first = self.repository.create_owner_session(locked, expires_at)
            second = self.repository.create_owner_session(locked, expires_at)
            deleted = self.repository.delete_owner_session(first.public_id)

        self.assertTrue(deleted)
        self.assertFalse(OwnerSession.objects.filter(public_id=first.public_id).exists())
        self.assertTrue(OwnerSession.objects.filter(public_id=second.public_id).exists())
        self.assertEqual(LineIdentity.objects.count(), 1)

    # テストケース: active ownerのrecipientを同じchannelへ2回作成する
    # 期待値: duplicate作成は同じ既存projectionへ収束し1行だけ残る
    def test_create_recipient_is_idempotent_for_identity_and_channel(self):
        _, identity = self.bind_owner()
        channel = self.create_channel()
        command = NewRecipient(
            identity_id=identity.public_id,
            channel_id=channel.public_id,
            friendship_state=DeliveryRecipient.FriendshipState.FRIEND,
        )

        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            first = self.repository.create_recipient(locked, command)
            second = self.repository.create_recipient(locked, command)

        self.assertEqual(first, second)
        self.assertEqual(DeliveryRecipient.objects.count(), 1)

    # テストケース: recipientの有効状態変更と対象削除を行う
    # 期待値: 対象行だけが変更・削除され他recipientとsessionを維持する
    def test_mutates_only_target_recipient(self):
        _, identity = self.bind_owner()
        expires_at = timezone.now() + timedelta(hours=8)
        channels = (self.create_channel(), self.create_channel())
        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            session = self.repository.create_owner_session(locked, expires_at)
            recipients = tuple(
                self.repository.create_recipient(
                    locked,
                    NewRecipient(
                        identity_id=identity.public_id,
                        channel_id=channel.public_id,
                        friendship_state=DeliveryRecipient.FriendshipState.UNKNOWN,
                    ),
                )
                for channel in channels
            )
            disabled = self.repository.set_recipient_enabled(
                locked, identity.public_id, recipients[0].public_id, False
            )
            deleted = self.repository.delete_recipient(
                locked, identity.public_id, recipients[0].public_id
            )

        self.assertFalse(disabled.enabled)
        self.assertTrue(deleted)
        self.assertFalse(
            DeliveryRecipient.objects.filter(public_id=recipients[0].public_id).exists()
        )
        self.assertTrue(
            DeliveryRecipient.objects.filter(public_id=recipients[1].public_id).exists()
        )
        self.assertTrue(OwnerSession.objects.filter(public_id=session.public_id).exists())

    # テストケース: pending ownerからrecipient mutationを要求する
    # 期待値: generation fence後はrecipientを変更せず通常操作を拒否する
    def test_rejects_recipient_mutation_after_unlink_fence(self):
        _, identity = self.bind_owner()
        channel = self.create_channel()
        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            self.repository.begin_unlink(locked, uuid4())

        with self.assertRaises(AccountStateError) as raised, transaction.atomic():
            pending = self.repository.lock_owner_account()
            self.repository.create_recipient(
                pending,
                NewRecipient(
                    identity_id=identity.public_id,
                    channel_id=channel.public_id,
                    friendship_state=DeliveryRecipient.FriendshipState.UNKNOWN,
                ),
            )

        self.assertEqual(raised.exception.code, "owner_not_active")
        self.assertEqual(DeliveryRecipient.objects.count(), 0)

    # テストケース: active ownerからunlink snapshotを取得してfenceを開始する
    # 期待値: identityとsorted recipient/channel IDを含む一貫したsnapshotと新generationが保存される
    def test_gets_unlink_snapshot_and_begins_new_generation(self):
        _, identity = self.bind_owner()
        channels = (self.create_channel(), self.create_channel())
        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            recipients = tuple(
                self.repository.create_recipient(
                    locked,
                    NewRecipient(
                        identity_id=identity.public_id,
                        channel_id=channel.public_id,
                        friendship_state=DeliveryRecipient.FriendshipState.UNKNOWN,
                    ),
                )
                for channel in reversed(channels)
            )
            snapshot = self.repository.get_unlink_snapshot(locked)
            generation = uuid4()
            pending = self.repository.begin_unlink(locked, generation)

        self.assertEqual(snapshot.owner_slot, 1)
        self.assertEqual(snapshot.identity_id, identity.public_id)
        self.assertEqual(snapshot.display_name, "Owner")
        self.assertEqual(
            snapshot.recipient_ids,
            tuple(sorted((item.public_id for item in recipients), key=str)),
        )
        self.assertEqual(
            snapshot.channel_ids,
            tuple(sorted((channel.public_id for channel in channels), key=str)),
        )
        self.assertEqual(pending.unlink_generation, generation)
        self.assertEqual(pending.state, OwnerAccount.State.DEAUTHORIZATION_PENDING)

    # テストケース: stale generationでLINE認可取消成功markerを保存する
    # 期待値: pending状態を変更せずunlink attempt staleとして拒否する
    def test_rejects_stale_generation_when_marking_line_deauthorized(self):
        owner, _, generation, _ = self.begin_local_deletion()

        with self.assertRaises(AccountStateError) as raised, transaction.atomic():
            locked = self.repository.lock_owner_account()
            self.repository.mark_line_deauthorized(
                locked, uuid4(), timezone.now() + timedelta(seconds=1)
            )

        self.assertEqual(raised.exception.code, "unlink_attempt_stale")
        stored = OwnerAccount.objects.get(slot=1)
        self.assertEqual(stored.unlink_generation, generation)
        self.assertEqual(stored.line_deauthorized_at, owner.line_deauthorized_at)

    # テストケース: expected generationでLINE認可取消成功markerを保存する
    # 期待値: 成功時刻とlocal deletion pendingが同じcommitへ保存される
    def test_marks_line_deauthorized_for_expected_generation(self):
        local_pending, _, generation, confirmed_at = self.begin_local_deletion()

        self.assertEqual(local_pending.state, OwnerAccount.State.LOCAL_DELETION_PENDING)
        self.assertEqual(local_pending.unlink_generation, generation)
        self.assertEqual(local_pending.line_deauthorized_at, confirmed_at)

    # テストケース: local deletion pendingをexpected generationでfinalizeする
    # 期待値: recipient・全session・identityだけを削除してownerをvacantへ戻す
    def test_finalizes_unlink_atomically_and_preserves_delivery_audit(self):
        _, identity = self.bind_owner()
        channel = self.create_channel()
        now = timezone.now()
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
        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            self.repository.create_owner_session(
                locked, timezone.now() + timedelta(hours=8)
            )
            self.repository.create_recipient(
                locked,
                NewRecipient(
                    identity_id=identity.public_id,
                    channel_id=channel.public_id,
                    friendship_state=DeliveryRecipient.FriendshipState.FRIEND,
                ),
            )
            generation = uuid4()
            pending = self.repository.begin_unlink(locked, generation)
            local_pending = self.repository.mark_line_deauthorized(
                pending, generation, timezone.now()
            )
            self.repository.finalize_unlink(local_pending, generation)

        owner = OwnerAccount.objects.get(slot=1)
        self.assertEqual(owner.state, OwnerAccount.State.VACANT)
        self.assertIsNone(owner.identity_id)
        self.assertIsNone(owner.unlink_generation)
        self.assertIsNone(owner.line_deauthorized_at)
        self.assertEqual(LineIdentity.objects.count(), 0)
        self.assertEqual(OwnerSession.objects.count(), 0)
        self.assertEqual(DeliveryRecipient.objects.count(), 0)
        audit.refresh_from_db()
        self.assertEqual(audit.subject, "監査対象")
        self.assertEqual(DeliveryAttempt.objects.count(), 1)

    # テストケース: stale generationでlocal finalizeを要求する
    # 期待値: identity・recipient・session・markerを一切変更しない
    def test_rejects_stale_generation_without_partial_deletion(self):
        _, identity, generation, confirmed_at = self.begin_local_deletion()

        with self.assertRaises(AccountStateError) as raised, transaction.atomic():
            locked = self.repository.lock_owner_account()
            self.repository.finalize_unlink(locked, uuid4())

        self.assertEqual(raised.exception.code, "unlink_attempt_stale")
        stored = OwnerAccount.objects.get(slot=1)
        self.assertEqual(stored.unlink_generation, generation)
        self.assertEqual(stored.line_deauthorized_at, confirmed_at)
        self.assertTrue(LineIdentity.objects.filter(public_id=identity.public_id).exists())

    # テストケース: 旧unlink完了後に新identity・session・recipientを再linkし、新generation開始後へ旧marker/finalizeを送る。
    # 期待値: 両方をstaleとして拒否し、再link後の全個人データと新generationを変更しない。
    def test_old_generation_cannot_mutate_relinked_identity_session_or_recipient(self):
        _, _, old_generation, _ = self.begin_local_deletion()
        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            self.repository.finalize_unlink(locked, old_generation)

        channel = self.create_channel()
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            new_identity = self.repository.upsert_identity(
                self.verified_identity(subject=f"U{uuid4().hex}", display_name="New")
            )
            owner = self.repository.bind_owner_identity(
                owner, new_identity.public_id
            )
            new_session = self.repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )
            new_recipient = self.repository.create_recipient(
                owner,
                NewRecipient(
                    new_identity.public_id,
                    channel.public_id,
                    DeliveryRecipient.FriendshipState.FRIEND,
                ),
            )
            new_generation = uuid4()
            self.repository.begin_unlink(owner, new_generation)

        for operation in ("marker", "finalize"):
            with self.subTest(operation=operation), self.assertRaises(
                AccountStateError
            ) as raised, transaction.atomic():
                locked = self.repository.lock_owner_account()
                if operation == "marker":
                    self.repository.mark_line_deauthorized(
                        locked, old_generation, timezone.now()
                    )
                else:
                    self.repository.finalize_unlink(locked, old_generation)
            self.assertEqual(raised.exception.code, "unlink_attempt_stale")

        owner = OwnerAccount.objects.get(slot=1)
        self.assertEqual(owner.identity.public_id, new_identity.public_id)
        self.assertEqual(owner.unlink_generation, new_generation)
        self.assertTrue(
            OwnerSession.objects.filter(public_id=new_session.public_id).exists()
        )
        self.assertTrue(
            DeliveryRecipient.objects.filter(public_id=new_recipient.public_id).exists()
        )

    # テストケース: recipient削除後のsession削除statementでDB障害を発生させる
    # 期待値: transaction全体をrollbackして全個人データと成功markerを保持する
    def test_finalize_failure_rolls_back_all_deletions_and_keeps_marker(self):
        _, identity = self.bind_owner()
        channel = self.create_channel()
        with transaction.atomic():
            locked = self.repository.lock_owner_account()
            self.repository.create_owner_session(
                locked, timezone.now() + timedelta(hours=8)
            )
            self.repository.create_recipient(
                locked,
                NewRecipient(
                    identity_id=identity.public_id,
                    channel_id=channel.public_id,
                    friendship_state=DeliveryRecipient.FriendshipState.FRIEND,
                ),
            )
            generation = uuid4()
            pending = self.repository.begin_unlink(locked, generation)
            confirmed_at = timezone.now()
            self.repository.mark_line_deauthorized(
                pending, generation, confirmed_at
            )

        with mock.patch.object(
            self.repository,
            "_delete_owner_sessions",
            side_effect=DatabaseError("fault"),
        ):
            with self.assertRaises(AccountPersistenceError) as raised:
                with transaction.atomic():
                    locked = self.repository.lock_owner_account()
                    self.repository.finalize_unlink(locked, generation)

        self.assertEqual(raised.exception.code, "storage_unavailable")
        stored = OwnerAccount.objects.get(slot=1)
        self.assertEqual(stored.state, OwnerAccount.State.LOCAL_DELETION_PENDING)
        self.assertEqual(stored.unlink_generation, generation)
        self.assertEqual(stored.line_deauthorized_at, confirmed_at)
        self.assertEqual(LineIdentity.objects.count(), 1)
        self.assertEqual(OwnerSession.objects.count(), 1)
        self.assertEqual(DeliveryRecipient.objects.count(), 1)


class AccountRepositoryTransactionTests(TransactionTestCase):
    reset_sequences = True

    # テストケース: transaction外でowner lockを取得する
    # 期待値: programming errorとして拒否してlockなしmutationを許可しない
    def test_owner_lock_requires_transaction(self):
        repository = DjangoAccountRepository()

        with self.assertRaises(AccountRepositoryProgrammingError) as raised:
            repository.lock_owner_account()

        self.assertEqual(raised.exception.code, "transaction_required")

    # テストケース: MySQLのlock timeoutまたはdeadlockをrepository境界で受け取る。
    # 期待値: raw DB例外を漏らさず同じretryable永続化結果へ分類する。
    def test_classifies_mysql_lock_timeout_and_deadlock_as_retryable(self):
        repository = DjangoAccountRepository()

        for error_code in (1205, 1213):
            with self.subTest(error_code=error_code):
                with self.assertRaises(AccountPersistenceError) as raised:
                    with repository._translate_database_errors():
                        raise OperationalError(error_code, "database-detail")
                self.assertEqual(raised.exception.code, "retryable")
                self.assertNotIn("database-detail", str(raised.exception))


class AccountRepositoryConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        OwnerAccount.objects.get_or_create(slot=1)

    @staticmethod
    def run_independently(function):
        close_old_connections()
        try:
            return function()
        finally:
            close_old_connections()

    def make_proof(self, subject):
        return VerifiedLineIdentity(
            provider_id="0012345678",
            subject=LineSubject(subject),
            display_name="Owner",
        )

    # テストケース: 異なる適格identityによる初回bindingを同時実行する
    # 期待値: owner lockにより1件だけがbindされ、敗者identityもrollbackされる
    def test_concurrent_first_binding_keeps_single_identity(self):
        barrier = threading.Barrier(2)

        def attempt(proof):
            barrier.wait(timeout=5)
            repository = DjangoAccountRepository()
            try:
                with transaction.atomic():
                    owner = repository.lock_owner_account()
                    identity = repository.upsert_identity(proof)
                    bound = repository.bind_owner_identity(owner, identity.public_id)
                return "bound", bound.identity_id
            except AccountStateError as error:
                return error.code, None

        proofs = (
            self.make_proof(f"U{uuid4().hex}"),
            self.make_proof(f"U{uuid4().hex}"),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                future.result(timeout=10)
                for future in (
                    executor.submit(self.run_independently, lambda: attempt(proofs[0])),
                    executor.submit(self.run_independently, lambda: attempt(proofs[1])),
                )
            )

        self.assertEqual(sorted(result[0] for result in results), ["bound", "identity_mismatch"])
        owner = OwnerAccount.objects.get(slot=1)
        self.assertIsNotNone(owner.identity_id)
        self.assertEqual(LineIdentity.objects.count(), 1)

    # テストケース: 同じidentity・channelのrecipient登録を同時実行する
    # 期待値: owner lockと一意制約により両要求が同じ1行へ収束する
    def test_concurrent_duplicate_recipient_creation_converges(self):
        repository = DjangoAccountRepository()
        proof = self.make_proof(f"U{uuid4().hex}")
        with transaction.atomic():
            owner = repository.lock_owner_account()
            identity = repository.upsert_identity(proof)
            repository.bind_owner_identity(owner, identity.public_id)
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label="通知チャネル",
            provider_id="0012345678",
            is_active=True,
        )
        command = NewRecipient(
            identity_id=identity.public_id,
            channel_id=channel.public_id,
            friendship_state=DeliveryRecipient.FriendshipState.UNKNOWN,
        )
        barrier = threading.Barrier(2)

        def create():
            barrier.wait(timeout=5)
            local_repository = DjangoAccountRepository()
            with transaction.atomic():
                locked = local_repository.lock_owner_account()
                recipient = local_repository.create_recipient(locked, command)
            return recipient.public_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            public_ids = tuple(
                future.result(timeout=10)
                for future in (
                    executor.submit(self.run_independently, create),
                    executor.submit(self.run_independently, create),
                )
            )

        self.assertEqual(public_ids[0], public_ids[1])
        self.assertEqual(DeliveryRecipient.objects.count(), 1)
