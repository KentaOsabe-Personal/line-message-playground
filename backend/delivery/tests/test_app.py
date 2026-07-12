from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase


class DeliveryAppTests(SimpleTestCase):
    # テストケース: delivery appの登録状態とDjango設定へのLINE秘密情報の非取込を確認する。
    # 期待値: delivery appが登録され、access token、channel secret、宛先IDが設定に存在しない。
    def test_delivery_app_is_registered_without_loading_line_secrets(self):
        app_config = apps.get_app_config("delivery")

        self.assertEqual(app_config.name, "delivery")
        self.assertIn("delivery", settings.INSTALLED_APPS)
        self.assertFalse(
            {
                "LINE_CHANNEL_ACCESS_TOKEN",
                "LINE_CHANNEL_SECRET",
                "LINE_USER_ID",
            }
            & vars(settings).keys()
        )
