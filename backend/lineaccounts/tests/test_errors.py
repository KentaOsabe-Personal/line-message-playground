from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.test import SimpleTestCase
from rest_framework.exceptions import AuthenticationFailed, ValidationError

from lineaccounts.errors import SafeAPIError, safe_exception_handler


class SafeErrorBoundaryTests(SimpleTestCase):
    def context(self):
        return {"view": object(), "request": object()}

    # テストケース: credentialを含む下位例外を共通変換境界へ渡す
    # 期待値: raw例外を固定のunexpected errorへ置換する
    def test_unknown_exception_is_replaced_without_secret_echo(self):
        canary = "line-error-secret-canary"

        response = safe_exception_handler(RuntimeError(canary), self.context())

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["error"]["code"], "unexpected")
        self.assertNotIn(canary, repr(response.data))

    # テストケース: field値を含むDRF validation errorを変換する
    # 期待値: field名だけを保持し、入力値を固定メッセージへ置換する
    def test_validation_error_preserves_only_safe_field_names(self):
        canary = "id-token-canary"

        response = safe_exception_handler(
            ValidationError({"idToken": [canary]}), self.context()
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertEqual(
            response.data["error"]["fields"],
            {"idToken": ["入力値が不正です。"]},
        )
        self.assertNotIn(canary, repr(response.data))

    # テストケース: authenticationと定義済みdomain errorを変換する
    # 期待値: 定義済みcode・summary・HTTP statusだけを返す
    def test_known_errors_map_to_safe_envelope(self):
        auth = safe_exception_handler(
            AuthenticationFailed("subject-canary"), self.context()
        )
        domain = safe_exception_handler(
            SafeAPIError("provider_mismatch"), self.context()
        )

        self.assertEqual(auth.status_code, 401)
        self.assertEqual(auth.data["error"]["code"], "authentication_required")
        self.assertEqual(domain.status_code, 422)
        self.assertEqual(domain.data["error"]["code"], "provider_mismatch")
        self.assertEqual(set(domain.data), {"error"})

    # テストケース: 未定義の公開error codeを構築する
    # 期待値: codeを公開せずprogramming errorとして拒否する
    def test_safe_api_error_rejects_unknown_code(self):
        with self.assertRaises(ValueError):
            SafeAPIError("raw_line_error")

    # テストケース: Django標準の404とpermission例外を共通変換境界へ渡す
    # 期待値: 500へ崩さず、安全な404/403 envelopeへ変換する
    def test_django_http_exceptions_keep_safe_status_contracts(self):
        canary = "django-error-secret-canary"

        not_found = safe_exception_handler(Http404(canary), self.context())
        denied = safe_exception_handler(
            DjangoPermissionDenied(canary), self.context()
        )

        self.assertEqual(not_found.status_code, 404)
        self.assertEqual(
            not_found.data["error"]["code"], "recipient_not_found"
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.data["error"]["code"], "owner_not_allowed")
        self.assertNotIn(canary, repr(not_found.data))
        self.assertNotIn(canary, repr(denied.data))
