from unittest.mock import Mock, patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIClient

from lineaccounts.container import (
    build_recipient_service,
    build_session_service,
    build_unlink_service,
)
from lineaccounts.runtime import get_line_account_runtime
from lineaccounts.views import (
    ChannelListAPIView,
    LineLoginAPIView,
    RecipientCollectionAPIView,
    RecipientDetailAPIView,
    SessionAPIView,
    UnlinkAPIView,
    UnlinkPreviewAPIView,
)
from linechannels.models import LineChannel


class AccountBackendIntegrationTests(TestCase):
    def setUp(self):
        runtime = get_line_account_runtime()
        LineChannel.objects.create(
            public_id=runtime.linked_channel_public_id,
            messaging_api_channel_id="1234567890",
            bot_user_id="U" + "a" * 32,
            label="LIFF direct",
            provider_id=runtime.provider_id,
            is_active=True,
        )

    # テストケース: すべてのaccount APIが共通URL配下の設計指定Viewへ接続される
    # 期待値: 固定endpointが対応するView classへ解決される
    def test_account_endpoints_are_mounted_under_common_api_prefix(self):
        routes = {
            "/api/account/session/": SessionAPIView,
            "/api/account/session/line/": LineLoginAPIView,
            "/api/account/channels/": ChannelListAPIView,
            "/api/account/recipients/": RecipientCollectionAPIView,
            "/api/account/recipients/00000000-0000-4000-8000-000000000001/": (
                RecipientDetailAPIView
            ),
            "/api/account/unlink-preview/": UnlinkPreviewAPIView,
            "/api/account/unlink/": UnlinkAPIView,
        }

        for path, expected_view in routes.items():
            with self.subTest(path=path):
                self.assertIs(resolve(path).func.view_class, expected_view)

    # テストケース: account APIへ共通の公開HTTPS安全設定を適用する
    # 期待値: cookie属性、exact trusted origin、safe exception handlerが設計値と一致する
    def test_account_runtime_uses_secure_shared_http_defaults(self):
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertTrue(settings.CSRF_COOKIE_SECURE)
        self.assertFalse(settings.CSRF_COOKIE_HTTPONLY)
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")
        self.assertEqual(
            settings.CSRF_TRUSTED_ORIGINS,
            ["https://test.example.ngrok.app"],
        )
        self.assertEqual(
            settings.REST_FRAMEWORK["EXCEPTION_HANDLER"],
            "lineaccounts.errors.safe_exception_handler",
        )
        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"],
            ["lineaccounts.authentication.OwnerSessionAuthentication"],
        )
        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"],
            ["lineaccounts.permissions.IsActiveOwner"],
        )

    # テストケース: account serviceを専用containerで明示的に合成する
    # 期待値: runtime、directory policy、gateway、repository、lockが設計順に注入される
    @patch("lineaccounts.container.DefaultAccountUnlinkService")
    @patch("lineaccounts.container.DefaultRecipientService")
    @patch("lineaccounts.container.DefaultAccountSessionService")
    @patch("lineaccounts.container.MySQLUnlinkExecutionLock")
    @patch("lineaccounts.container.HttpxLinePlatformGateway")
    @patch("lineaccounts.container.DjangoAccountRepository")
    @patch("lineaccounts.container.DjangoLineChannelDirectory")
    @patch("lineaccounts.container.resolve_liff_linked_channel_policy")
    @patch("lineaccounts.container.get_line_account_runtime")
    def test_container_composes_all_account_services(
        self,
        get_runtime,
        resolve_policy,
        directory_factory,
        repository_factory,
        gateway_factory,
        lock_factory,
        session_service_factory,
        recipient_service_factory,
        unlink_service_factory,
    ):
        runtime = Mock(owner_eligibility=object())
        directory = object()
        repository = object()
        gateway = object()
        policy = object()
        lock = object()
        get_runtime.return_value = runtime
        directory_factory.return_value = directory
        repository_factory.return_value = repository
        gateway_factory.return_value = gateway
        resolve_policy.return_value = policy
        lock_factory.return_value = lock

        build_session_service()
        build_recipient_service()
        build_unlink_service()

        session_service_factory.assert_called_once_with(
            gateway, repository, runtime.owner_eligibility
        )
        recipient_service_factory.assert_called_once_with(
            directory, repository, gateway, policy
        )
        unlink_service_factory.assert_called_once_with(
            gateway, repository, lock, directory
        )
        self.assertEqual(resolve_policy.call_count, 3)

    # テストケース: LIFF直結チャネルpolicyが不正な状態でsession statusを取得する。
    # 期待値: account状態を公開せず、安全な503へfail closedになる。
    def test_session_status_fails_closed_when_linked_channel_policy_is_invalid(self):
        with patch(
            "lineaccounts.views.build_session_service",
            side_effect=ImproperlyConfigured("unsafe-policy-detail"),
        ):
            response = APIClient().get("/api/account/session/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "storage_unavailable")
        self.assertNotIn("unsafe-policy-detail", str(response.json()))

    # テストケース: 匿名利用者が全account endpointへ到達する
    # 期待値: 公開statusだけが応答し、保護endpointは入力処理前に401となる
    def test_account_endpoints_boot_with_shared_authentication_boundary(self):
        client = APIClient(enforce_csrf_checks=True)
        bootstrap = client.get("/api/account/session/")
        csrf = bootstrap.cookies["csrftoken"].value
        unsafe_headers = {
            "HTTP_ORIGIN": settings.CSRF_TRUSTED_ORIGINS[0],
            "HTTP_X_CSRFTOKEN": csrf,
        }

        self.assertEqual(bootstrap.status_code, 200)
        self.assertEqual(bootstrap.json(), {"state": "anonymous"})

        requests = (
            client.delete("/api/account/session/", **unsafe_headers),
            client.get("/api/account/channels/"),
            client.post(
                "/api/account/recipients/", {}, format="json", **unsafe_headers
            ),
            client.patch(
                "/api/account/recipients/00000000-0000-4000-8000-000000000001/",
                {},
                format="json",
                **unsafe_headers,
            ),
            client.delete(
                "/api/account/recipients/00000000-0000-4000-8000-000000000001/",
                **unsafe_headers,
            ),
            client.post(
                "/api/account/unlink-preview/", {}, format="json", **unsafe_headers
            ),
            client.post(
                "/api/account/unlink/", {}, format="json", **unsafe_headers
            ),
        )

        self.assertTrue(all(response.status_code == 401 for response in requests))
