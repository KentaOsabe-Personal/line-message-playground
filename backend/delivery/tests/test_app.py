import os

from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase


class DeliveryAppTests(SimpleTestCase):
    # テストケース: delivery appの登録状態とBackend-only環境変数の設定配線を確認する。
    # 期待値: access tokenと固定宛先だけを読み込み、pushに不要なchannel secretは取り込まない。
    def test_delivery_app_loads_only_required_line_settings(self):
        app_config = apps.get_app_config("delivery")

        self.assertEqual(app_config.name, "delivery")
        self.assertIn("delivery", settings.INSTALLED_APPS)
        self.assertEqual(settings.LINE_CHANNEL_ACCESS_TOKEN, os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))
        self.assertEqual(settings.LINE_USER_ID, os.getenv("LINE_USER_ID", ""))
        self.assertNotIn("LINE_CHANNEL_SECRET", vars(settings))
