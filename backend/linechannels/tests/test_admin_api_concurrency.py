import threading
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lineaccounts.authentication import OWNER_SESSION_KEY
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import OwnerAccount
from lineaccounts.repositories import DjangoAccountRepository
from lineaccounts.types import LineSubject
from linechannels.container import build_channel_admin_service, build_line_channel_service
from linechannels.types import RegisterLineChannel
from linechannels.validators import build_credential_pair


class BlockingAdminRepository:
    def __init__(self, delegate, method):
        self.delegate = delegate
        self.method = method
        self.reached = threading.Event()
        self.release = threading.Event()

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def _block(self, name, result):
        if self.method == name:
            self.reached.set()
            if not self.release.wait(5):
                raise RuntimeError("admin repository release timed out")
        return result

    def list_for_owner_provider(self, provider_id):
        return self._block("list", self.delegate.list_for_owner_provider(provider_id))

    def get_for_owner_provider(self, channel_id, provider_id):
        return self._block("detail", self.delegate.get_for_owner_provider(channel_id, provider_id))


class BlockingFoundationService:
    def __init__(self, delegate):
        self.delegate = delegate
        self.reached = threading.Event()
        self.release = threading.Event()

    def register(self, command):
        return self.delegate.register(command)

    def update(self, command):
        result = self.delegate.update(command)
        self.reached.set()
        if not self.release.wait(5):
            raise RuntimeError("foundation release timed out")
        return result


class AdminAPIConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        OwnerAccount.objects.get_or_create(slot=1)
        self.origin = "https://test.example.ngrok.app"
        self.repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.identity = self.repository.upsert_identity(
                VerifiedLineIdentity(
                    provider_id="0012345678",
                    subject=LineSubject(f"U{uuid4().hex}"),
                    display_name="Owner",
                )
            )
            owner = self.repository.bind_owner_identity(owner, self.identity.public_id)
            self.session = self.repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )
        result = build_line_channel_service().register(
            RegisterLineChannel(
                messaging_api_channel_id=str(uuid4().int)[:20],
                bot_user_id=f"U{uuid4().hex}",
                label="競合対象",
                credentials=build_credential_pair("race-token-canary", "race-secret-canary"),
                is_active=True,
                provider_id="0012345678",
            )
        )
        self.assertEqual(result.status, "succeeded")
        self.channel_id = result.channel.public_id

    def owner_client(self):
        client = APIClient(enforce_csrf_checks=True)
        session = client.session
        session[OWNER_SESSION_KEY] = str(self.session.public_id)
        session.save()
        bootstrap = client.get("/api/account/session/")
        return client, bootstrap.cookies["csrftoken"].value

    def run_race(self, request, blocker, invalidate):
        responses = []
        invalidations = []

        def run_request():
            close_old_connections()
            try:
                responses.append(request())
            finally:
                close_old_connections()

        def run_invalidation():
            close_old_connections()
            try:
                invalidations.append(invalidate())
            finally:
                close_old_connections()

        request_thread = threading.Thread(target=run_request)
        request_thread.start()
        self.assertTrue(blocker.reached.wait(5))
        invalidation_thread = threading.Thread(target=run_invalidation)
        invalidation_thread.start()
        blocker.release.set()
        request_thread.join(5)
        invalidation_thread.join(5)
        self.assertFalse(request_thread.is_alive())
        self.assertFalse(invalidation_thread.is_alive())
        self.assertEqual(len(responses), 1)
        self.assertEqual(len(invalidations), 1)
        return responses[0]

    def delete_session(self):
        with transaction.atomic():
            return DjangoAccountRepository().delete_owner_session(self.session.public_id)

    def begin_unlink(self):
        with transaction.atomic():
            repository = DjangoAccountRepository()
            owner = repository.lock_owner_account()
            repository.begin_unlink(owner, uuid4())
        return "unlinked"

    # テストケース: 一覧投影中に同じowner sessionの失効を競合させる
    # 期待値: 先行readは完了し、失効後のreadは401となり管理情報を返さない
    def test_list_linearizes_before_concurrent_session_invalidation(self):
        client, _ = self.owner_client()
        service = build_channel_admin_service()
        blocker = BlockingAdminRepository(service._repository, "list")
        service._repository = blocker
        with patch("linechannels.admin_views.build_channel_admin_service", return_value=service):
            response = self.run_race(
                lambda: client.get("/api/line/channels/"), blocker, self.delete_session
            )
        self.assertEqual(response.status_code, 200)
        denied = client.get("/api/line/channels/")
        self.assertEqual(denied.status_code, 401)
        self.assertNotIn(str(self.channel_id), str(denied.json()))

    # テストケース: 詳細投影中にowner unlink開始を競合させる
    # 期待値: 先行readだけが完了し、unlink後の詳細はowner fenceで拒否される
    def test_detail_linearizes_before_concurrent_unlink(self):
        client, _ = self.owner_client()
        service = build_channel_admin_service()
        blocker = BlockingAdminRepository(service._repository, "detail")
        service._repository = blocker
        with patch("linechannels.admin_views.build_channel_admin_service", return_value=service):
            response = self.run_race(
                lambda: client.get(f"/api/line/channels/{self.channel_id}/"),
                blocker,
                self.begin_unlink,
            )
        self.assertEqual(response.status_code, 200)
        denied = client.get(f"/api/line/channels/{self.channel_id}/")
        self.assertIn(denied.status_code, (401, 403))
        self.assertNotIn(str(self.channel_id), str(denied.json()))

    # テストケース: metadata mutationがchannel更新後もowner transactionを保持する間にsession失効を競合させる
    # 期待値: 先行mutationを完全commitしてから失効し、後続mutationは401でDBを変更しない
    def test_mutation_linearizes_before_concurrent_session_invalidation(self):
        client, csrf = self.owner_client()
        current = client.get(f"/api/line/channels/{self.channel_id}/").json()
        service = build_channel_admin_service()
        blocker = BlockingFoundationService(service._foundation_service)
        service._foundation_service = blocker
        with patch("linechannels.admin_views.build_channel_admin_service", return_value=service):
            response = self.run_race(
                lambda: client.patch(
                    f"/api/line/channels/{self.channel_id}/",
                    {"expectedUpdatedAt": current["updatedAt"], "label": "先行更新"},
                    format="json",
                    HTTP_ORIGIN=self.origin,
                    HTTP_X_CSRFTOKEN=csrf,
                ),
                blocker,
                self.delete_session,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["label"], "先行更新")
        denied = client.patch(
            f"/api/line/channels/{self.channel_id}/",
            {"expectedUpdatedAt": response.json()["updatedAt"], "label": "禁止更新"},
            format="json",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(denied.status_code, 401)

