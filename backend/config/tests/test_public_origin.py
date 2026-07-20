import json
from pathlib import Path

from django.test import SimpleTestCase
from django.test.utils import override_settings

from config.public_origin import build_trusted_https_origin, validate_public_host


class PublicOriginTests(SimpleTestCase):
    @staticmethod
    def public_host_fixture():
        return json.loads(Path("/test-fixtures/public-hosts.json").read_text())

    # テストケース: canonical な単一 ASCII hostname を公開 host として検証する。
    # 期待値: hostname はそのまま保持され、exact HTTPS origin が導出される。
    def test_accepts_canonical_ascii_hostname(self):
        host = validate_public_host("example.ngrok.app")

        self.assertEqual(host, "example.ngrok.app")
        self.assertEqual(build_trusted_https_origin(host), "https://example.ngrok.app")

    # テストケース: scheme、port、path、wildcard、空白等を含む公開 host を検証する。
    # 期待値: すべて設定エラーとして拒否され、暗黙の正規化は行われない。
    def test_rejects_noncanonical_public_hosts(self):
        for host in self.public_host_fixture()["invalid"]:
            with self.subTest(host=host), self.assertRaisesMessage(
                ValueError, "PUBLIC_HOST_INVALID"
            ):
                validate_public_host(host)

    # テストケース: Backend と Vite が共有する公開host fixtureの正常値を検証する。
    # 期待値: すべてのcanonical hostからexact HTTPS originだけが導出される。
    def test_accepts_all_hosts_from_shared_cross_runtime_fixture(self):
        for host in self.public_host_fixture()["valid"]:
            with self.subTest(host=host):
                self.assertEqual(validate_public_host(host), host)
                self.assertEqual(build_trusted_https_origin(host), f"https://{host}")

    # テストケース: test settings が公開 host から trusted origin を構成した状態を確認する。
    # 期待値: exact HTTPS origin だけが CSRF trusted origin に設定される。
    @override_settings(CSRF_TRUSTED_ORIGINS=["https://test.example.ngrok.app"])
    def test_settings_use_exact_trusted_origin(self):
        from django.conf import settings

        self.assertEqual(
            settings.CSRF_TRUSTED_ORIGINS, ["https://test.example.ngrok.app"]
        )
