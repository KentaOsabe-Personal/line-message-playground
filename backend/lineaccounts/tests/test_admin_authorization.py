import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from lineaccounts.admin_authorization import (
    DjangoOwnerOperationFence,
    OwnerOperationContext,
)
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import OwnerAccount
from lineaccounts.repositories import DjangoAccountRepository
from lineaccounts.repositories import (
    AccountPersistenceError,
    AccountRepositoryProgrammingError,
    LockedOwnerAccount,
    LockedOwnerSession,
)
from lineaccounts.types import LineSubject


class OwnerOperationFenceTests(TestCase):
    def setUp(self):
        OwnerAccount.objects.get_or_create(slot=1)
        self.repository = DjangoAccountRepository()
        self.fence = DjangoOwnerOperationFence(self.repository)
        self.now = timezone.now()
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            identity = self.repository.upsert_identity(
                VerifiedLineIdentity(
                    provider_id="000123",
                    subject=LineSubject("U" + uuid4().hex),
                    display_name="Owner",
                )
            )
            owner = self.repository.bind_owner_identity(owner, identity.public_id)
            self.session = self.repository.create_owner_session(
                owner, self.now + timedelta(hours=1)
            )
            self.identity = identity

    # テストケース: 有効なowner sessionとidentityで管理操作fenceを取得する
    # 期待値: request値ではなくDBに保存されたidentity providerをactive proofとして返す
    def test_active_session_returns_database_derived_provider_proof(self):
        with transaction.atomic():
            result = self.fence.lock_active(
                OwnerOperationContext(self.session.public_id, self.identity.public_id),
                self.now,
            )

        self.assertEqual(result.status, "active")
        self.assertEqual(result.provider_id, "000123")
        self.assertEqual(result.identity_public_id, self.identity.public_id)

    # テストケース: identity不一致、session期限切れ、unlink中のownerでfenceを取得する
    # 期待値: provider proofを返さずauthentication requiredまたはoperation blockedへ収束する
    def test_expired_mismatched_or_unlinking_session_is_safely_rejected(self):
        cases = (
            (
                OwnerOperationContext(self.session.public_id, uuid4()),
                self.now,
                "authentication_required",
            ),
            (
                OwnerOperationContext(self.session.public_id, self.identity.public_id),
                self.now + timedelta(hours=2),
                "authentication_required",
            ),
        )
        for context, now, expected in cases:
            with self.subTest(expected=expected), transaction.atomic():
                result = self.fence.lock_active(context, now)
            self.assertEqual(result.code, expected)

        OwnerAccount.objects.filter(slot=1).update(
            state=OwnerAccount.State.DEAUTHORIZATION_PENDING,
            unlink_generation=uuid4(),
        )
        with transaction.atomic():
            result = self.fence.lock_active(
                OwnerOperationContext(self.session.public_id, self.identity.public_id),
                self.now,
            )
        self.assertEqual(result.code, "owner_operation_blocked")


class OwnerOperationFenceContractTests(TransactionTestCase):
    def setUp(self):
        self.identity_id = uuid4()
        self.session_id = uuid4()
        self.now = timezone.now()
        self.owner = LockedOwnerAccount(
            slot=1,
            state=OwnerAccount.State.ACTIVE,
            identity_id=self.identity_id,
            unlink_generation=None,
            line_deauthorized_at=None,
        )
        self.session = LockedOwnerSession(
            public_id=self.session_id,
            owner_slot=1,
            identity_id=self.identity_id,
            provider_id="000123",
            owner_state=OwnerAccount.State.ACTIVE,
            expires_at=self.now + timedelta(hours=1),
        )

    # テストケース: transaction外とtransaction内で管理操作fenceを取得する
    # 期待値: transaction外を拒否し、transaction内ではownerからsessionの順にlockする
    def test_requires_caller_transaction_and_calls_owner_before_session(self):
        repository = Mock()
        calls = []
        repository.lock_owner_account.side_effect = lambda: (
            calls.append("owner") or self.owner
        )
        repository.lock_owner_session.side_effect = lambda *args: (
            calls.append("session") or self.session
        )
        fence = DjangoOwnerOperationFence(repository)
        context = OwnerOperationContext(self.session_id, self.identity_id)

        with self.assertRaises(AccountRepositoryProgrammingError):
            fence.lock_active(context, self.now)
        with transaction.atomic():
            result = fence.lock_active(context, self.now)

        self.assertEqual(result.status, "active")
        self.assertEqual(calls, ["owner", "session"])

    # テストケース: owner lockがretryableまたは一般storage失敗を返す
    # 期待値: sessionを読まず、生DB情報のない固定safe codeへ分類する
    def test_classifies_retryable_and_unavailable_storage_without_raw_errors(self):
        for source_code, expected in (
            ("retryable", "storage_retryable"),
            ("storage_unavailable", "storage_unavailable"),
        ):
            with self.subTest(source_code=source_code):
                repository = Mock()
                repository.lock_owner_account.side_effect = AccountPersistenceError(
                    source_code
                )
                fence = DjangoOwnerOperationFence(repository)
                with transaction.atomic():
                    result = fence.lock_active(
                        OwnerOperationContext(self.session_id, self.identity_id),
                        self.now,
                    )
                self.assertEqual((result.status, result.code), ("failed", expected))
                repository.lock_owner_session.assert_not_called()


class OwnerOperationFenceConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        OwnerAccount.objects.get_or_create(slot=1)
        self.repository = DjangoAccountRepository()
        self.now = timezone.now()
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.identity = self.repository.upsert_identity(
                VerifiedLineIdentity(
                    provider_id="000123",
                    subject=LineSubject("U" + uuid4().hex),
                    display_name="Owner",
                )
            )
            owner = self.repository.bind_owner_identity(owner, self.identity.public_id)
            self.session = self.repository.create_owner_session(
                owner, self.now + timedelta(hours=1)
            )
        self.context = OwnerOperationContext(
            self.session.public_id, self.identity.public_id
        )

    # テストケース: active proof取得中に別transactionからunlinkを開始する
    # 期待値: 先行proof完了後にunlinkが進み、後続proofはoperation blockedになる
    def test_unlink_linearizes_after_inflight_proof_and_blocks_later_proofs(self):
        proof_ready = threading.Event()
        release_proof = threading.Event()
        unlink_started = threading.Event()

        def read_proof():
            close_old_connections()
            try:
                with transaction.atomic():
                    result = DjangoOwnerOperationFence(
                        DjangoAccountRepository()
                    ).lock_active(self.context, self.now)
                    proof_ready.set()
                    self.assertTrue(release_proof.wait(5))
                    return result
            finally:
                close_old_connections()

        def begin_unlink():
            close_old_connections()
            try:
                self.assertTrue(proof_ready.wait(5))
                unlink_started.set()
                with transaction.atomic():
                    repository = DjangoAccountRepository()
                    owner = repository.lock_owner_account()
                    return repository.begin_unlink(owner, uuid4())
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            read_future = executor.submit(read_proof)
            unlink_future = executor.submit(begin_unlink)
            self.assertTrue(proof_ready.wait(5))
            self.assertTrue(unlink_started.wait(5))
            self.assertFalse(unlink_future.done())
            release_proof.set()
            first = read_future.result(timeout=5)
            pending = unlink_future.result(timeout=5)

        self.assertEqual(first.status, "active")
        self.assertEqual(pending.state, OwnerAccount.State.DEAUTHORIZATION_PENDING)
        with transaction.atomic():
            later = DjangoOwnerOperationFence(self.repository).lock_active(
                self.context, timezone.now()
            )
        self.assertEqual(later.code, "owner_operation_blocked")

    # テストケース: active proof取得中に別transactionから対象sessionを削除する
    # 期待値: 先行proof完了後に削除が進み、後続proofはauthentication requiredになる
    def test_session_deletion_linearizes_after_inflight_proof(self):
        proof_ready = threading.Event()
        release_proof = threading.Event()

        def read_proof():
            close_old_connections()
            try:
                with transaction.atomic():
                    result = DjangoOwnerOperationFence(
                        DjangoAccountRepository()
                    ).lock_active(self.context, self.now)
                    proof_ready.set()
                    self.assertTrue(release_proof.wait(5))
                    return result
            finally:
                close_old_connections()

        def delete_session():
            close_old_connections()
            try:
                self.assertTrue(proof_ready.wait(5))
                with transaction.atomic():
                    return DjangoAccountRepository().delete_owner_session(
                        self.session.public_id
                    )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            read_future = executor.submit(read_proof)
            delete_future = executor.submit(delete_session)
            self.assertTrue(proof_ready.wait(5))
            self.assertFalse(delete_future.done())
            release_proof.set()
            first = read_future.result(timeout=5)
            deleted = delete_future.result(timeout=5)

        self.assertEqual(first.status, "active")
        self.assertTrue(deleted)
        with transaction.atomic():
            later = DjangoOwnerOperationFence(self.repository).lock_active(
                self.context, timezone.now()
            )
        self.assertEqual(later.code, "authentication_required")
