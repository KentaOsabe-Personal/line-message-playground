from django.conf import settings
from django.middleware.csrf import get_token
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from lineaccounts.csrf import enforce_exact_origin_and_csrf
from lineaccounts.errors import SafeAPIError


class ExactOriginCsrfTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory(enforce_csrf_checks=True)
        self.origin = settings.CSRF_TRUSTED_ORIGINS[0]

    def valid_request(self):
        seed = self.factory.get("/api/account/session/")
        masked_token = get_token(seed)
        secret = seed.META["CSRF_COOKIE"]
        request = self.factory.post(
            "/api/account/session/line/",
            {"idToken": "opaque"},
            format="json",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=masked_token,
        )
        request.COOKIES[settings.CSRF_COOKIE_NAME] = secret
        return request

    # テストケース: canonical originと一致するOriginおよびcookie/header tokenでPOSTする
    # 期待値: exact OriginとDjango CSRF検証を通過する
    def test_accepts_only_exact_origin_with_matching_csrf_tokens(self):
        enforce_exact_origin_and_csrf(self.valid_request())

    # テストケース: missing・複数値・null・scheme/host/port差異のOriginでPOSTする
    # 期待値: CSRF処理前にすべて同じ安全なcsrf_failedとして拒否する
    def test_rejects_non_exact_origin_forms(self):
        invalid_origins = (
            None,
            f"{self.origin},{self.origin}",
            "null",
            self.origin.replace("https://", "http://"),
            self.origin.replace("test.example.ngrok.app", "other.example"),
            f"{self.origin}:443",
        )
        for origin in invalid_origins:
            with self.subTest(origin=origin):
                request = self.valid_request()
                if origin is None:
                    request.META.pop("HTTP_ORIGIN", None)
                else:
                    request.META["HTTP_ORIGIN"] = origin
                with self.assertRaises(SafeAPIError) as raised:
                    enforce_exact_origin_and_csrf(request)
                self.assertEqual(raised.exception.code, "csrf_failed")

    # テストケース: exact Originでもcookie/header CSRF tokenが欠落または不一致でPOSTする
    # 期待値: mutation前に同じ安全なcsrf_failedとして拒否する
    def test_rejects_missing_and_mismatched_csrf_tokens(self):
        missing = self.valid_request()
        missing.COOKIES.clear()
        mismatch = self.valid_request()
        mismatch.META["HTTP_X_CSRFTOKEN"] = "x" * 64

        for request in (missing, mismatch):
            with self.assertRaises(SafeAPIError) as raised:
                enforce_exact_origin_and_csrf(request)
            self.assertEqual(raised.exception.code, "csrf_failed")

    # テストケース: 公開HTTPS origin用のsession/CSRF cookie設定を確認する
    # 期待値: secure・same-site属性と必要最小限のHttpOnly差異が固定される
    def test_uses_secure_session_and_csrf_cookie_settings(self):
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertTrue(settings.CSRF_COOKIE_SECURE)
        self.assertFalse(settings.CSRF_COOKIE_HTTPONLY)
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")
