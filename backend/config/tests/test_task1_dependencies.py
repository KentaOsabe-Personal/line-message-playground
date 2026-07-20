from importlib.metadata import version

from django.test import SimpleTestCase


class TaskOneDependencyTests(SimpleTestCase):
    # テストケース: Backend の同期 HTTP client 依存バージョンを確認する。
    # 期待値: HTTPX 0.28.1 が import 可能な固定依存として導入されている。
    def test_httpx_is_pinned_to_designated_version(self):
        self.assertEqual(version("httpx"), "0.28.1")
