from django.test import SimpleTestCase
from django.test.utils import override_settings

from config.public_origin import build_trusted_https_origin, validate_public_host


class PublicOriginTests(SimpleTestCase):
    # テストケース: canonical な単一 ASCII hostname を公開 host として検証する。
    # 期待値: hostname はそのまま保持され、exact HTTPS origin が導出される。
    def test_accepts_canonical_ascii_hostname(self):
        host = validate_public_host("example.ngrok.app")

        self.assertEqual(host, "example.ngrok.app")
        self.assertEqual(build_trusted_https_origin(host), "https://example.ngrok.app")

    # テストケース: scheme、port、path、wildcard、空白等を含む公開 host を検証する。
    # 期待値: すべて設定エラーとして拒否され、暗黙の正規化は行われない。
    def test_rejects_noncanonical_public_hosts(self):
        invalid_hosts = (
            "",
            "https://example.ngrok.app",
            "example.ngrok.app:443",
            "example.ngrok.app/liff",
            "*.ngrok.app",
            " example.ngrok.app",
            "example.ngrok.app ",
            "example..ngrok.app",
            "-example.ngrok.app",
            "example_.ngrok.app",
            "例え.jp",
        )

        for host in invalid_hosts:
            with self.subTest(host=host), self.assertRaisesMessage(
                ValueError, "PUBLIC_HOST_INVALID"
            ):
                validate_public_host(host)

    # テストケース: test settings が公開 host から trusted origin を構成した状態を確認する。
    # 期待値: exact HTTPS origin だけが CSRF trusted origin に設定される。
    @override_settings(CSRF_TRUSTED_ORIGINS=["https://test.example.ngrok.app"])
    def test_settings_use_exact_trusted_origin(self):
        from django.conf import settings

        self.assertEqual(
            settings.CSRF_TRUSTED_ORIGINS, ["https://test.example.ngrok.app"]
        )

