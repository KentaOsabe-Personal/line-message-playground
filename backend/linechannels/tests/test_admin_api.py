from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from django.test import SimpleTestCase, override_settings
from django.urls import resolve, reverse
from rest_framework.test import APIClient, APITestCase

from lineaccounts.authentication import OwnerPrincipal
from linechannels.admin_presenters import AdminPresenter
from linechannels.admin_types import (
    AdminChannelMutationSucceeded,
    AdminChannelView,
    AdminServiceFailed,
    ChannelDeleteSucceeded,
    ChannelListSucceeded,
    ChannelReadSucceeded,
    ConnectionCheckCompleted,
)


NOW = datetime(2026, 8, 1, 3, 4, 5, tzinfo=timezone.utc)
CHANNEL_ID = UUID("12345678-1234-4234-8234-123456789abc")


def channel_view():
    return AdminChannelView(
        public_id=CHANNEL_ID,
        messaging_api_channel_id="1234567890",
        bot_user_id="U" + "a" * 32,
        label="通知チャネル",
        provider_id="0012345678",
        is_active=True,
        credentials_state="configured",
        credentials_updated_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


class AdminPresenterTests(SimpleTestCase):
    # テストケース: safe read modelを公開originとreverse済みingress pathで表示する
    # 期待値: 全非秘密fieldとHTTPS Webhook URLだけを返しrequest Hostや内部情報を使わない
    @override_settings(PUBLIC_HOST="public.example.ngrok.app")
    def test_presents_exact_safe_channel_dto(self):
        dto = AdminPresenter().channel(channel_view())

        self.assertEqual(
            set(dto),
            {
                "channelId", "label", "messagingApiChannelId", "botUserId",
                "providerId", "active", "credentialsState", "credentialsUpdatedAt",
                "createdAt", "updatedAt", "webhookUrl",
            },
        )
        self.assertEqual(dto["webhookUrl"], f"https://public.example.ngrok.app/api/line/webhooks/{CHANNEL_ID}/")
        self.assertNotIn("cipher", str(dto).lower())
        self.assertNotIn("token", str(dto).lower())
        self.assertNotIn("secret", str(dto).lower())


class AdminAPITests(APITestCase):
    def setUp(self):
        self.origin = "https://test.example.ngrok.app"
        self.principal = OwnerPrincipal(uuid4(), uuid4(), "active")
        self.client = APIClient()
        self.client.force_authenticate(self.principal)
        self.service = Mock()
        self.patch = patch("linechannels.admin_views.build_channel_admin_service", return_value=self.service)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def unsafe(self, method, path, body):
        return getattr(self.client, method)(path, body, format="json", HTTP_ORIGIN=self.origin)

    # テストケース: owner sessionのない利用者が管理一覧を要求する
    # 期待値: serviceを呼ぶ前に401で拒否しチャネル情報を返さない
    def test_anonymous_request_is_rejected_before_service(self):
        self.client.force_authenticate(user=None)

        response = self.client.get("/api/line/channels/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_required")
        self.service.list_channels.assert_not_called()

    # テストケース: ownerが一覧・詳細・登録・更新の主要endpointを操作する
    # 期待値: service結果がexact safe DTOと201/200へ変換されowner contextが渡る
    def test_read_and_primary_mutation_endpoints_return_safe_dtos(self):
        self.service.list_channels.return_value = ChannelListSucceeded((channel_view(),))
        self.service.get_channel.return_value = ChannelReadSucceeded(channel_view())
        self.service.register.return_value = AdminChannelMutationSucceeded(channel_view())
        self.service.update.return_value = AdminChannelMutationSucceeded(channel_view())

        listed = self.client.get("/api/line/channels/")
        detailed = self.client.get(f"/api/line/channels/{CHANNEL_ID}/")
        created = self.unsafe("post", "/api/line/channels/", {
            "label": "通知チャネル", "messagingApiChannelId": "1234567890",
            "botUserId": "U" + "a" * 32, "providerId": "0012345678",
            "accessToken": "api-token-canary", "channelSecret": "api-secret-canary", "active": True,
        })
        updated = self.unsafe("patch", f"/api/line/channels/{CHANNEL_ID}/", {
            "expectedUpdatedAt": NOW.isoformat(), "label": "更新後",
        })

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(detailed.status_code, 200)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(updated.status_code, 200)
        for response in (listed, detailed, created, updated):
            self.assertNotIn("api-token-canary", str(response.json()))
            self.assertNotIn("api-secret-canary", str(response.json()))

    # テストケース: ownerが状態変更・削除・接続確認を実行する
    # 期待値: safe resultだけを返し接続結果は限定scope付き200になる
    def test_action_endpoints_map_safe_results(self):
        self.service.set_state.return_value = AdminChannelMutationSucceeded(channel_view())
        self.service.delete.return_value = ChannelDeleteSucceeded(CHANNEL_ID, "通知チャネル")
        self.service.check_connection.return_value = ConnectionCheckCompleted("connected", NOW)

        state = self.unsafe("post", f"/api/line/channels/{CHANNEL_ID}/state/", {"expectedUpdatedAt": NOW.isoformat(), "active": False})
        deleted = self.unsafe("delete", f"/api/line/channels/{CHANNEL_ID}/", {"expectedUpdatedAt": NOW.isoformat()})
        checked = self.unsafe("post", f"/api/line/channels/{CHANNEL_ID}/connection-check/", {})

        self.assertEqual(state.status_code, 200)
        self.assertEqual(deleted.json(), {"channelId": str(CHANNEL_ID), "label": "通知チャネル", "deleted": True})
        self.assertEqual(checked.json(), {"channelId": str(CHANNEL_ID), "status": "connected", "checkedAt": NOW.isoformat().replace("+00:00", "Z"), "scope": "access_token_and_bot_identity_only"})

    # テストケース: serviceの各safe failure分類をHTTP境界へ返す
    # 期待値: 固定status/codeへ一貫して写像され外部分類や秘密値を含めない
    def test_maps_service_failures_to_fixed_http_contract(self):
        cases = (
            ("invalid_input", "validation_error", 400),
            ("authentication_required", "authentication_required", 401),
            ("owner_operation_blocked", "owner_operation_blocked", 403),
            ("channel_not_found", "channel_not_found", 404),
            ("duplicate_channel", "duplicate_channel", 409),
            ("stale_channel", "stale_channel", 409),
            ("channel_referenced", "channel_referenced", 409),
            ("provider_mismatch", "provider_mismatch", 422),
            ("provider_immutable", "provider_immutable", 422),
            ("credential_unavailable", "credential_unavailable", 422),
            ("storage_retryable", "storage_retryable", 503),
            ("storage_unavailable", "storage_unavailable", 503),
        )
        for code, expected_code, expected_status in cases:
            with self.subTest(code=code):
                self.service.get_channel.return_value = AdminServiceFailed(code)
                response = self.client.get(f"/api/line/channels/{CHANNEL_ID}/")
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["error"]["code"], expected_code)

    # テストケース: 登録・更新・状態変更で暗号化または保存資格情報の読取が失敗する
    # 期待値: 内部codeを固定422/503へ縮約し500、生例外、資格情報を応答へ含めない
    def test_mutations_fail_closed_for_credential_crypto_failures(self):
        create_body = {
            "label": "通知チャネル",
            "messagingApiChannelId": "1234567890",
            "botUserId": "U" + "a" * 32,
            "providerId": "0012345678",
            "accessToken": "crypto-token-canary",
            "channelSecret": "crypto-secret-canary",
            "active": True,
        }
        cases = (
            (
                "register",
                "post",
                "/api/line/channels/",
                create_body,
                "encryption_failed",
                503,
                "storage_unavailable",
            ),
            (
                "update",
                "patch",
                f"/api/line/channels/{CHANNEL_ID}/",
                {
                    "expectedUpdatedAt": NOW.isoformat(),
                    "accessToken": "crypto-token-canary",
                    "channelSecret": "crypto-secret-canary",
                },
                "credential_unreadable",
                422,
                "credential_unavailable",
            ),
            (
                "set_state",
                "post",
                f"/api/line/channels/{CHANNEL_ID}/state/",
                {
                    "expectedUpdatedAt": NOW.isoformat(),
                    "active": True,
                    "accessToken": "crypto-token-canary",
                    "channelSecret": "crypto-secret-canary",
                },
                "encryption_failed",
                503,
                "storage_unavailable",
            ),
        )
        for service_method, method, path, body, failure, status_code, public_code in cases:
            with self.subTest(service_method=service_method):
                getattr(self.service, service_method).return_value = AdminServiceFailed(failure)
                response = self.unsafe(method, path, body)
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()["error"]["code"], public_code)
                self.assertNotIn("crypto-token-canary", str(response.json()))
                self.assertNotIn("crypto-secret-canary", str(response.json()))

    # テストケース: 将来の未知なservice failure分類がHTTP境界へ到達する
    # 期待値: code構築例外を起こさず秘密なしのstorage_unavailableへfail closedする
    def test_unknown_service_failure_fails_closed(self):
        self.service.get_channel.return_value = AdminServiceFailed(
            "future_internal_failure"
        )

        response = self.client.get(f"/api/line/channels/{CHANNEL_ID}/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "storage_unavailable")

    # テストケース: 不正originとunknown request fieldでunsafe endpointを呼ぶ
    # 期待値: service前に403 CSRFまたは400 exact validationで拒否される
    def test_mutations_validate_origin_and_shape_before_service(self):
        bad_origin = self.client.post("/api/line/channels/", {}, format="json", HTTP_ORIGIN="https://evil.example")
        unknown = self.unsafe("post", f"/api/line/channels/{CHANNEL_ID}/connection-check/", {"accessToken": "leak-canary"})
        self.assertEqual(bad_origin.status_code, 403)
        self.assertEqual(bad_origin.json()["error"]["code"], "csrf_failed")
        self.assertEqual(unknown.status_code, 400)
        self.assertNotIn("leak-canary", str(unknown.json()))
        self.service.check_connection.assert_not_called()


class AdminRouteTests(SimpleTestCase):
    # テストケース: 管理APIの全7 method/endpointをURLConfから解決する
    # 期待値: collection/detail/state/connection-checkが相対api配下の管理Viewへ解決される
    def test_all_admin_routes_resolve(self):
        paths = (
            "/api/line/channels/", f"/api/line/channels/{CHANNEL_ID}/",
            f"/api/line/channels/{CHANNEL_ID}/state/",
            f"/api/line/channels/{CHANNEL_ID}/connection-check/",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertIn("AdminChannel", resolve(path).func.view_class.__name__)
        self.assertEqual(reverse("linechannels:admin-collection"), "/api/line/channels/")
