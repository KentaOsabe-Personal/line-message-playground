from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import transaction
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lineaccounts.authentication import OWNER_SESSION_KEY
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.repositories import DjangoAccountRepository
from lineaccounts.types import LineSubject
from lineaccounts.unlink_services import (
    UnlinkCompleted,
    UnlinkPendingReauthentication,
    UnlinkPreview,
)


class _Service:
    def __init__(self):
        self.execute_result = UnlinkCompleted()
        self.execute_args = None

    def preview(self, principal, now):
        return UnlinkPreview(
            display_name="Owner",
            recipient_count=0,
            channel_labels=(),
            delivery_audit_retained=True,
            confirmation_token="opaque-confirmation",
            expires_at=now + timedelta(minutes=5),
        )

    def execute(self, principal, confirmation_token, user_access_token, now):
        self.execute_args = (principal, confirmation_token, user_access_token, now)
        return self.execute_result


class UnlinkAPITests(TestCase):
    def setUp(self):
        self.origin = "https://test.example.ngrok.app"
        repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = repository.lock_owner_account()
            identity = repository.upsert_identity(
                VerifiedLineIdentity(
                    "0012345678", LineSubject(f"U{uuid4().hex}"), "Owner"
                )
            )
            owner = repository.bind_owner_identity(owner, identity.public_id)
            self.owner_session = repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )
        self.service = _Service()
        self.patch = patch("lineaccounts.views.build_unlink_service", return_value=self.service)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def owner_client(self):
        client = APIClient(enforce_csrf_checks=True)
        session = client.session
        session[OWNER_SESSION_KEY] = str(self.owner_session.public_id)
        session.save()
        bootstrap = client.get("/api/account/session/")
        return client, bootstrap.cookies["csrftoken"].value

    def post(self, client, path, body, csrf):
        return client.post(
            path,
            body,
            format="json",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=csrf,
        )

    # テストケース: active ownerがpreviewと初回unlinkを実行する
    # 期待値: safe camelCase unionを返しcredentialをresponseへ含めない
    def test_preview_and_execute_return_safe_contracts(self):
        client, csrf = self.owner_client()
        preview = self.post(client, "/api/account/unlink-preview/", {}, csrf)
        executed = self.post(
            client,
            "/api/account/unlink/",
            {
                "confirmationToken": "opaque-confirmation",
                "userAccessToken": "user-token-canary",
            },
            csrf,
        )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["recipientCount"], 0)
        self.assertEqual(preview.json()["deliveryAuditRetained"], True)
        self.assertEqual(executed.status_code, 200)
        self.assertEqual(executed.json(), {"state": "completed"})
        self.assertNotIn("user-token-canary", str(executed.json()))

    # テストケース: unlinkがLINE未確認pendingへ収束する
    # 期待値: 202とfresh再認証actionを返し完了表示しない
    def test_execute_returns_pending_union(self):
        client, csrf = self.owner_client()
        self.service.execute_result = UnlinkPendingReauthentication()

        response = self.post(
            client,
            "/api/account/unlink/",
            {"confirmationToken": "opaque", "userAccessToken": "fresh"},
            csrf,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(),
            {
                "state": "pending",
                "stage": "deauthorization_pending",
                "retryAction": "reauthenticate",
            },
        )

    # テストケース: 匿名requestまたは未知field付きunlinkを送信する
    # 期待値: service前にowner認証またはstrict validationで拒否する
    def test_authentication_and_strict_request_precede_service(self):
        anonymous = APIClient(enforce_csrf_checks=True)
        bootstrap = anonymous.get("/api/account/session/")
        denied = self.post(
            anonymous,
            "/api/account/unlink/",
            {"confirmationToken": "x", "userAccessToken": "y", "userId": "U-secret"},
            bootstrap.cookies["csrftoken"].value,
        )
        client, csrf = self.owner_client()
        invalid = self.post(
            client,
            "/api/account/unlink/",
            {"userId": "U-secret"},
            csrf,
        )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(invalid.status_code, 400)
        self.assertNotIn("U-secret", str(invalid.json()))

    # テストケース: pending stageでpreviewまたは許可外fieldを要求する
    # 期待値: previewはpermission、解除bodyはstage別strict validationでservice前に拒否する
    def test_pending_stage_blocks_preview_and_rejects_disallowed_fields(self):
        client, csrf = self.owner_client()
        repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = repository.lock_owner_account()
            repository.begin_unlink(owner, uuid4())

        preview = self.post(client, "/api/account/unlink-preview/", {}, csrf)
        invalid_resume = self.post(
            client,
            "/api/account/unlink/",
            {"confirmationToken": "stale", "userAccessToken": "fresh"},
            csrf,
        )

        self.assertEqual(preview.status_code, 403)
        self.assertEqual(invalid_resume.status_code, 400)
        self.assertIsNone(self.service.execute_args)

    # テストケース: LIFF直結チャネルpolicyを確立できない状態でunlinkする
    # 期待値: sagaを構築・実行せず秘密なしの503へfail closedする
    def test_unlink_fails_closed_when_linked_channel_policy_is_invalid(self):
        self.patch.stop()
        client, csrf = self.owner_client()

        with patch(
            "lineaccounts.container.resolve_liff_linked_channel_policy",
            side_effect=ImproperlyConfigured("unsafe-config-canary"),
        ):
            response = self.post(
                client,
                "/api/account/unlink/",
                {
                    "confirmationToken": "opaque-confirmation",
                    "userAccessToken": "fresh",
                },
                csrf,
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "storage_unavailable")
        self.assertNotIn("unsafe-config-canary", str(response.json()))
