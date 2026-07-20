from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed

from lineaccounts.authentication import (
    OWNER_SESSION_KEY,
    OwnerPrincipal,
    OwnerSessionAuthentication,
)
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.repositories import DjangoAccountRepository
from lineaccounts.permissions import CanResumeUnlink, HasOwnerSession, IsActiveOwner
from lineaccounts.types import LineSubject


class OwnerAuthBoundaryTests(TestCase):
    def setUp(self):
        self.repository = DjangoAccountRepository()
        self.now = timezone.now()
        identity = VerifiedLineIdentity(
            provider_id="0012345678",
            subject=LineSubject(f"U{uuid4().hex}"),
            display_name="Owner",
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            stored = self.repository.upsert_identity(identity)
            owner = self.repository.bind_owner_identity(owner, stored.public_id)
            self.session = self.repository.create_owner_session(
                owner, self.now + timedelta(hours=8)
            )

    def authenticate(self, value):
        boundary = OwnerSessionAuthentication(
            self.repository, clock=lambda: self.now
        )
        return boundary.authenticate(
            SimpleNamespace(session={OWNER_SESSION_KEY: value})
        )

    # テストケース: 有効なopaque owner session IDで認証する
    # 期待値: identity public IDとactive stateだけを持つowner principalを返す
    def test_authenticates_active_owner_from_session_ledger(self):
        result = self.authenticate(str(self.session.public_id))

        self.assertIsNotNone(result)
        principal, context = result
        self.assertEqual(principal.owner_session_id, self.session.public_id)
        self.assertEqual(principal.identity_public_id, self.session.identity_id)
        self.assertEqual(principal.account_state, "active")
        self.assertEqual(context.session, self.session)

    # テストケース: session cookieにowner session IDが存在しない
    # 期待値: 匿名として扱いowner principalを生成しない
    def test_missing_owner_session_remains_anonymous(self):
        boundary = OwnerSessionAuthentication(
            self.repository, clock=lambda: self.now
        )

        self.assertIsNone(boundary.authenticate(SimpleNamespace(session={})))

    # テストケース: malformed・存在しない・期限切れowner session IDで認証する
    # 期待値: 安全なauthentication failureとして拒否し別端末sessionへ影響しない
    def test_rejects_invalid_and_expired_session_before_permission(self):
        other = None
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            other = self.repository.create_owner_session(
                owner, self.now + timedelta(hours=8)
            )
            expired = self.repository.create_owner_session(
                owner, self.now - timedelta(seconds=1)
            )

        for value in ("not-a-uuid", str(uuid4()), str(expired.public_id)):
            with self.subTest(value=value), self.assertRaises(AuthenticationFailed):
                self.authenticate(value)

        self.assertIsNotNone(
            self.repository.get_session(other.public_id, self.now)
        )

    # テストケース: unlink pending sessionへ通常操作と再開permissionを評価する
    # 期待値: active通常操作を拒否しstatus/logout/unlink再開用permissionだけを許可する
    def test_pending_owner_is_fenced_from_normal_operations(self):
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.repository.begin_unlink(owner, uuid4())

        principal, _ = self.authenticate(str(self.session.public_id))
        request = SimpleNamespace(user=principal)

        self.assertFalse(IsActiveOwner().has_permission(request, None))
        self.assertTrue(CanResumeUnlink().has_permission(request, None))
        self.assertTrue(HasOwnerSession().has_permission(request, None))

    # テストケース: active owner sessionへ通常操作permissionを評価する
    # 期待値: active ownerだけが通常の配信・管理操作を許可される
    def test_active_owner_permission_rejects_anonymous_and_allows_owner(self):
        principal, _ = self.authenticate(str(self.session.public_id))

        self.assertTrue(
            IsActiveOwner().has_permission(SimpleNamespace(user=principal), None)
        )
        self.assertFalse(
            IsActiveOwner().has_permission(SimpleNamespace(user=None), None)
        )
        self.assertIsInstance(principal, OwnerPrincipal)
