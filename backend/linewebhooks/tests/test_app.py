from django.apps import apps
from django.test import SimpleTestCase


class LineWebhooksAppTests(SimpleTestCase):
    # テストケース: Backend の標準設定で linewebhooks app を検索する
    # 期待値: 専用 AppConfig が登録済み app として解決される
    def test_app_is_registered(self) -> None:
        config = apps.get_app_config("linewebhooks")

        self.assertEqual(config.name, "linewebhooks")
        self.assertEqual(
            type(config).__qualname__,
            "LineWebhooksConfig",
        )
