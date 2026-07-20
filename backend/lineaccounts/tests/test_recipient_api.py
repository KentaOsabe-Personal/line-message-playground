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
from lineaccounts.models import DeliveryRecipient
from lineaccounts.recipient_services import (
    DefaultRecipientService,
    RecipientMutationFailed,
)
from lineaccounts.repositories import (
    AccountPersistenceError,
    AccountStateError,
    DjangoAccountRepository,
    NewRecipient,
)
from lineaccounts.runtime import LiffLinkedChannelPolicy
from lineaccounts.types import LineSubject
from linechannels.models import LineChannel
from linechannels.repositories import DjangoLineChannelDirectory


class _NoCallGateway:
    def __init__(self):
        self.calls = 0

    def verify_user_access_token(self, token, expected_subject):
        self.calls += 1
        raise AssertionError("LINE must not be called")

    def get_friendship(self, token):
        self.calls += 1
        raise AssertionError("LINE must not be called")


class RecipientAPITests(TestCase):
    def setUp(self):
        self.origin = "https://test.example.ngrok.app"
        self.provider_id = "0012345678"
        self.repository = DjangoAccountRepository()
        identity = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(f"U{uuid4().hex}"),
            display_name="Owner",
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.identity = self.repository.upsert_identity(identity)
            owner = self.repository.bind_owner_identity(
                owner, self.identity.public_id
            )
            self.owner_session = self.repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )
        self.direct = self.channel("LIFF direct")
        self.non_direct = self.channel("通知チャネル")
        self.gateway = _NoCallGateway()
        self.service = DefaultRecipientService(
            DjangoLineChannelDirectory(),
            self.repository,
            self.gateway,
            LiffLinkedChannelPolicy(self.direct.public_id),
        )
        self.service_patch = patch(
            "lineaccounts.views.build_recipient_service",
            return_value=self.service,
        )
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)

    def channel(self, label, *, active=True, provider_id=None):
        return LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label=label,
            provider_id=provider_id or self.provider_id,
            is_active=active,
        )

    def owner_client(self):
        client = APIClient(enforce_csrf_checks=True)
        session = client.session
        session[OWNER_SESSION_KEY] = str(self.owner_session.public_id)
        session.save()
        response = client.get("/api/account/session/")
        return client, response.cookies["csrftoken"].value

    def unsafe(self, client, method, path, body, token):
        return getattr(client, method)(
            path,
            body,
            format="json",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=token,
        )

    # テストケース: active ownerが登録候補と既存link一覧を取得する
    # 期待値: channel/recipient opaque IDと安全な状態だけを返しLINE user IDを含めない
    def test_active_owner_lists_safe_channel_projections(self):
        client, _ = self.owner_client()

        response = client.get("/api/account/channels/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 2)
        body = str(response.json())
        self.assertNotIn("messaging_api_channel_id", body)
        self.assertNotIn("bot_user_id", body)
        self.assertNotIn("subject", body)

    # テストケース: non-direct channelを登録・disable・enable・解除する
    # 期待値: strict safe projectionで状態遷移し解除は204、LINEを呼ばない
    def test_owner_executes_full_recipient_lifecycle(self):
        client, token = self.owner_client()
        created = self.unsafe(
            client,
            "post",
            "/api/account/recipients/",
            {"channelId": str(self.non_direct.public_id)},
            token,
        )
        recipient_id = created.json()["recipientId"]
        disabled = self.unsafe(
            client,
            "patch",
            f"/api/account/recipients/{recipient_id}/",
            {"enabled": False},
            token,
        )
        enabled = self.unsafe(
            client,
            "patch",
            f"/api/account/recipients/{recipient_id}/",
            {"enabled": True},
            token,
        )
        deleted = client.delete(
            f"/api/account/recipients/{recipient_id}/",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["friendshipState"], "unknown")
        self.assertFalse(created.json()["deliveryAvailable"])
        self.assertEqual(disabled.json()["linkState"], "linked_disabled")
        self.assertEqual(enabled.json()["linkState"], "linked_enabled")
        self.assertFalse(enabled.json()["deliveryAvailable"])
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(DeliveryRecipient.objects.exists())
        self.assertEqual(self.gateway.calls, 0)

    # テストケース: 未認証者が不正bodyでrecipient登録を要求する
    # 期待値: serializerやserviceより先に401で拒否し保護データを返さない
    def test_anonymous_is_rejected_before_recipient_validation(self):
        client = APIClient(enforce_csrf_checks=True)
        bootstrap = client.get("/api/account/session/")
        token = bootstrap.cookies["csrftoken"].value

        response = self.unsafe(
            client,
            "post",
            "/api/account/recipients/",
            {"userId": "forbidden"},
            token,
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json()["error"]["code"], "authentication_required"
        )
        self.assertFalse(DeliveryRecipient.objects.exists())

    # テストケース: unlink pending ownerがrecipient一覧を要求する
    # 期待値: serviceを呼ぶ前に403で通常管理操作を拒否する
    def test_pending_owner_is_rejected_before_recipient_service(self):
        client, _ = self.owner_client()
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.repository.begin_unlink(owner, uuid4())

        with patch.object(
            self.service,
            "list_channels",
            side_effect=AssertionError("service must not run"),
        ):
            response = client.get("/api/account/channels/")

        self.assertEqual(response.status_code, 403)
        self.assertIn(
            response.json()["error"]["code"],
            ("owner_not_allowed", "owner_operation_blocked"),
        )

    # テストケース: ownerがuserId未知fieldまたは不正CSRFで登録する
    # 期待値: userIdは400 validation、CSRF不備はそれより先に403で拒否する
    def test_registration_rejects_user_id_and_enforces_csrf_first(self):
        client, token = self.owner_client()
        invalid_body = {"userId": "forbidden"}

        bad_csrf = client.post(
            "/api/account/recipients/",
            invalid_body,
            format="json",
            HTTP_ORIGIN=self.origin,
        )
        strict = self.unsafe(
            client,
            "post",
            "/api/account/recipients/",
            invalid_body,
            token,
        )

        self.assertEqual(bad_csrf.status_code, 403)
        self.assertEqual(bad_csrf.json()["error"]["code"], "csrf_failed")
        self.assertEqual(strict.status_code, 400)
        self.assertEqual(strict.json()["error"]["code"], "validation_error")
        self.assertFalse(DeliveryRecipient.objects.exists())

    # テストケース: inactive channel登録とmissing recipient変更を要求する
    # 期待値: domain結果をそれぞれ422 channel_unavailableと404へ安全に変換する
    def test_maps_domain_failures_to_safe_http_statuses(self):
        inactive = self.channel("inactive", active=False)
        client, token = self.owner_client()

        unavailable = self.unsafe(
            client,
            "post",
            "/api/account/recipients/",
            {"channelId": str(inactive.public_id)},
            token,
        )
        missing = self.unsafe(
            client,
            "patch",
            f"/api/account/recipients/{uuid4()}/",
            {"enabled": False},
            token,
        )

        self.assertEqual(unavailable.status_code, 422)
        self.assertEqual(
            unavailable.json()["error"]["code"], "channel_unavailable"
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["error"]["code"], "recipient_not_found"
        )

    # テストケース: recipient DELETEへ未知fieldを含むbodyを送る
    # 期待値: strict empty request境界で400拒否し対象recipientを削除しない
    def test_delete_rejects_non_empty_request_before_unlink(self):
        client, token = self.owner_client()
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            recipient = self.repository.create_recipient(
                owner,
                NewRecipient(
                    identity_id=self.identity.public_id,
                    channel_id=self.non_direct.public_id,
                    friendship_state="unknown",
                ),
            )

        response = client.delete(
            f"/api/account/recipients/{recipient.public_id}/",
            {"userId": "forbidden"},
            format="json",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertTrue(
            DeliveryRecipient.objects.filter(public_id=recipient.public_id).exists()
        )

    # テストケース: permission後にunlink fenceが入りrecipient mutationが競合する
    # 期待値: raw owner_not_active/500ではなくunlink_in_progress 409へ収束する
    def test_maps_owner_fence_race_to_safe_409(self):
        client, token = self.owner_client()
        with patch.object(
            self.service,
            "unlink",
            side_effect=AccountStateError("owner_not_active"),
        ):
            response = client.delete(
                f"/api/account/recipients/{uuid4()}/",
                HTTP_ORIGIN=self.origin,
                HTTP_X_CSRFTOKEN=token,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"], "unlink_in_progress"
        )

    # テストケース: recipient APIのconfig・storage・LINE依存が利用不能になる
    # 期待値: 秘密やraw例外を返さず全てsafe 503へ収束する
    def test_maps_recipient_dependencies_to_safe_503(self):
        client, token = self.owner_client()
        with patch(
            "lineaccounts.views.build_recipient_service",
            side_effect=ImproperlyConfigured("private-config-detail"),
        ):
            config_failure = client.get("/api/account/channels/")
        with patch.object(
            self.service,
            "list_channels",
            side_effect=AccountPersistenceError("storage_unavailable"),
        ):
            storage_failure = client.get("/api/account/channels/")
        with patch.object(
            self.service,
            "register",
            return_value=RecipientMutationFailed("line_unavailable"),
        ):
            line_failure = self.unsafe(
                client,
                "post",
                "/api/account/recipients/",
                {"channelId": str(self.non_direct.public_id)},
                token,
            )

        for response in (config_failure, storage_failure, line_failure):
            self.assertEqual(response.status_code, 503)
            self.assertIn(
                response.json()["error"]["code"],
                ("storage_unavailable", "line_unavailable"),
            )
            self.assertNotIn("private-config-detail", str(response.json()))
