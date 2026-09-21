from django.conf import settings
from django.test import SimpleTestCase

from textjudgmentlab.runtime import LabRuntimeDisabled


class TextJudgmentLabSettingsTests(SimpleTestCase):
    # テストケース: ラボ設定なしの既存test settingsを起動する。
    # 期待値: 管理機能を妨げず、settingsには無効なラボruntimeが注入される。
    def test_existing_environment_starts_with_lab_disabled(self):
        self.assertIsInstance(settings.TEXT_JUDGMENT_LAB_RUNTIME, LabRuntimeDisabled)
        self.assertIn("textjudgmentlab.apps.TextJudgmentLabConfig", settings.INSTALLED_APPS)
