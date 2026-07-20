import base64
import os
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.test import SimpleTestCase


class TestSettingsTests(SimpleTestCase):
    # テストケース: 明示的なテスト設定で Django を初期化する
    # 期待値: DEBUG は無効で、プロセス専用の有効な Fernet 鍵が環境へ注入される
    def test_injects_ephemeral_credential_key_before_base_settings_load(self):
        raw_key = os.environ["LINE_CHANNEL_CREDENTIAL_KEYS"]

        self.assertFalse(settings.DEBUG)
        self.assertEqual(len(base64.urlsafe_b64decode(raw_key)), 32)

    # テストケース: Django settings の公開属性を列挙する
    # 期待値: 資格情報用の raw keyring は settings 属性として保持されない
    def test_does_not_publish_raw_credential_keyring_as_django_setting(self):
        self.assertFalse(hasattr(settings, "LINE_CHANNEL_CREDENTIAL_KEYS"))

    # テストケース: base settings読込前に供給されたsynthetic LINE runtime値を確認する。
    # 期待値: canonical host・channel・provider・UUIDが揃い、生成secretそのものはsourceへ固定保存されない。
    def test_bootstrap_supplies_canonical_synthetic_runtime_without_fixed_secret(self):
        self.assertEqual(os.environ["NGROK_DOMAIN"], "test.example.ngrok.app")
        self.assertEqual(os.environ["LINE_LOGIN_CHANNEL_ID"], "1234567890")
        self.assertEqual(os.environ["LINE_LOGIN_PROVIDER_ID"], "0012345678")
        UUID(os.environ["LINE_LIFF_LINKED_CHANNEL_PUBLIC_ID"])

        source = Path(settings.BASE_DIR, "config", "test_settings.py").read_text()
        self.assertNotIn(os.environ["DJANGO_SECRET_KEY"], source)
        self.assertNotIn(os.environ["LINE_LOGIN_CHANNEL_SECRET"], source)
        self.assertNotIn(os.environ["LINE_CHANNEL_CREDENTIAL_KEYS"], source)
