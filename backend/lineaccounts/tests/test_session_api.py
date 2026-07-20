from unittest.mock import patch
from uuid import uuid4

from django.db import transaction
from django.contrib.sessions.backends.db import SessionStore
from django.test import TestCase
from rest_framework.test import APIClient

from lineaccounts.gateway import VerifiedLineIdentity, VerifyIdentitySucceeded
from lineaccounts.models import LineIdentity, OwnerSession
from lineaccounts.repositories import AccountPersistenceError, DjangoAccountRepository
from lineaccounts.runtime import OwnerEligibilityDigest, derive_owner_digest
from lineaccounts.session_services import DefaultAccountSessionService
from lineaccounts.types import LineSubject


class _GatewayStub:
    def __init__(self, identity):
        self.identity = identity

    def verify_id_token(self, token):
        return VerifyIdentitySucceeded(self.identity)


class SessionAPITests(TestCase):
    def setUp(self):
        self.origin = "https://test.example.ngrok.app"
        self.subject = f"U{uuid4().hex}"
        self.identity = VerifiedLineIdentity(
            provider_id="0012345678",
            subject=LineSubject(self.subject),
            display_name="Owner",
        )
        self.service = DefaultAccountSessionService(
            _GatewayStub(self.identity),
            DjangoAccountRepository(),
            OwnerEligibilityDigest(
                derive_owner_digest("0012345678", self.subject)
            ),
        )
        self.service_patch = patch(
            "lineaccounts.views.build_session_service",
            return_value=self.service,
        )
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)

    def csrf_client(self):
        client = APIClient(enforce_csrf_checks=True)
        response = client.get("/api/account/session/")
        token = response.cookies["csrftoken"].value
        return client, token

    def login(self, client, token, proof="proof"):
        return client.post(
            "/api/account/session/line/",
            {"idToken": proof},
            format="json",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=token,
        )

    # テストケース: session APIを匿名状態で取得する
    # 期待値: LINE識別子なしのanonymous unionとCSRF bootstrap cookieを返す
    def test_get_bootstraps_csrf_cookie_and_returns_anonymous(self):
        client = APIClient(enforce_csrf_checks=True)

        response = client.get("/api/account/session/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"state": "anonymous"})
        self.assertIn("csrftoken", response.cookies)

    # テストケース: raw ID tokenでloginして同じ端末のstatusを確認する
    # 期待値: session keyをrotateしてopaque ledger IDだけを保存し安全なprofileを返す
    def test_login_rotates_session_and_returns_safe_authenticated_status(self):
        client, token = self.csrf_client()
        old_key = client.session.session_key

        login = self.login(client, token)
        status = client.get("/api/account/session/")

        self.assertEqual(login.status_code, 200)
        self.assertEqual(
            login.json(),
            {
                "state": "authenticated",
                "profile": {"displayName": "Owner", "linked": True},
            },
        )
        self.assertNotEqual(client.session.session_key, old_key)
        self.assertEqual(status.json(), login.json())
        self.assertNotIn(self.subject, str(login.json()))

    # テストケース: 2端末でlogin後に現在端末だけlogoutする
    # 期待値: current ledgerとcookieだけを終了し他端末sessionとidentityを維持する
    def test_logout_ends_only_current_device_session(self):
        first, first_token = self.csrf_client()
        second, second_token = self.csrf_client()
        self.login(first, first_token, "proof-one")
        self.login(second, second_token, "proof-two")
        token = first.cookies["csrftoken"].value

        logout = first.delete(
            "/api/account/session/",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.json(), {"state": "anonymous"})
        self.assertEqual(OwnerSession.objects.count(), 1)
        self.assertEqual(LineIdentity.objects.count(), 1)
        self.assertEqual(
            second.get("/api/account/session/").json()["state"],
            "authenticated",
        )

    # テストケース: ledger作成後のDjango session保存が失敗する
    # 期待値: authenticatedを返さずsafe 503へ収束しowner cookieを保存しない
    def test_login_fails_closed_when_django_session_save_fails(self):
        client, token = self.csrf_client()

        with patch.object(SessionStore, "save", side_effect=RuntimeError("db")):
            response = self.login(client, token)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "storage_unavailable")
        self.assertEqual(OwnerSession.objects.count(), 1)
        self.assertEqual(
            client.get("/api/account/session/").json(), {"state": "anonymous"}
        )

    # テストケース: session status・login・logoutでrepository障害が発生する
    # 期待値: raw例外を公開せず全endpointでstorage_unavailable 503へ収束する
    def test_maps_session_service_storage_failures_to_safe_503(self):
        client, token = self.csrf_client()
        failure = AccountPersistenceError("storage_unavailable")

        with patch.object(self.service, "get_status", side_effect=failure):
            status = client.get("/api/account/session/")
        with patch.object(self.service, "establish", side_effect=failure):
            login = self.login(client, token)

        self.login(client, token)
        token = client.cookies["csrftoken"].value
        with patch.object(self.service, "logout", side_effect=failure):
            logout = client.delete(
                "/api/account/session/",
                HTTP_ORIGIN=self.origin,
                HTTP_X_CSRFTOKEN=token,
            )

        for response in (status, login, logout):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.json()["error"]["code"], "storage_unavailable"
            )

    # テストケース: owner cookie付きlogoutのauthentication DB参照が失敗する
    # 期待値: handler到達前の障害もunexpected 500ではなくstorage_unavailable 503になる
    def test_maps_authentication_storage_failure_to_safe_503(self):
        client, token = self.csrf_client()
        self.login(client, token)
        token = client.cookies["csrftoken"].value

        with patch.object(
            DjangoAccountRepository,
            "get_session",
            side_effect=AccountPersistenceError("storage_unavailable"),
        ):
            response = client.delete(
                "/api/account/session/",
                HTTP_ORIGIN=self.origin,
                HTTP_X_CSRFTOKEN=token,
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"], "storage_unavailable"
        )

    # テストケース: unlink pending中のowner sessionでstatusを取得する
    # 期待値: 通常操作可能とはせずstage固有の安全な再開actionだけを返す
    def test_get_returns_pending_unlink_status(self):
        client, token = self.csrf_client()
        self.login(client, token)
        with transaction.atomic():
            owner = DjangoAccountRepository().lock_owner_account()
            DjangoAccountRepository().begin_unlink(owner, uuid4())

        response = client.get("/api/account/session/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "state": "unlinking",
                "stage": "deauthorization_pending",
                "retryAction": "reauthenticate",
            },
        )

    # テストケース: 匿名端末からlogoutを要求する
    # 期待値: handler mutationへ進まずauthentication_required 401で拒否する
    def test_anonymous_logout_is_rejected_before_handler(self):
        client, token = self.csrf_client()

        response = client.delete(
            "/api/account/session/",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json()["error"]["code"], "authentication_required"
        )

    # テストケース: 不正bodyと不正Origin/CSRFを同時にlogin endpointへ送る
    # 期待値: serializerより先にcsrf_failedとなり、有効CSRF時だけvalidation_errorになる
    def test_login_enforces_origin_and_csrf_before_request_validation(self):
        client, token = self.csrf_client()

        missing_origin = client.post(
            "/api/account/session/line/", {}, format="json"
        )
        missing_csrf = client.post(
            "/api/account/session/line/",
            {},
            format="json",
            HTTP_ORIGIN=self.origin,
        )
        valid_protection = client.post(
            "/api/account/session/line/",
            {},
            format="json",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(missing_origin.status_code, 403)
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(
            missing_origin.json()["error"]["code"], "csrf_failed"
        )
        self.assertEqual(
            missing_csrf.json()["error"]["code"], "csrf_failed"
        )
        self.assertEqual(valid_protection.status_code, 400)
        self.assertEqual(
            valid_protection.json()["error"]["code"], "validation_error"
        )
