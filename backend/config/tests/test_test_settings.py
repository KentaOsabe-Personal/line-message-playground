import base64
import os

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
