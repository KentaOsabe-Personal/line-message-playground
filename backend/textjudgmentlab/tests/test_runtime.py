from django.test import SimpleTestCase

from textjudgmentlab.runtime import (
    LabRuntimeConfigured,
    LabRuntimeDisabled,
    LabRuntimeUnavailable,
    load_lab_runtime,
)


class LabRuntimeTests(SimpleTestCase):
    def valid_environment(self) -> dict[str, str]:
        return {
            "TEXT_JUDGMENT_LAB_ENABLED": "true",
            "TEXT_JUDGMENT_LAB_CHANNEL_ID": "0012345678",
            "TEXT_JUDGMENT_LAB_OWNER_DIGEST": "a" * 64,
            "TEXT_JUDGMENT_LAB_ORIGIN": "https://lab.example.test",
            "TYPESAFE_API_KEY": "test-typesafe-secret",
            "TEXT_JUDGMENT_LAB_MODEL": "jev-1.13.0",
        }

    # テストケース: enabledが未設定またはfalseの設定を読む。
    # 期待値: 他の設定値を要求せず、ラボだけが無効状態になる。
    def test_defaults_to_disabled_without_requiring_lab_settings(self):
        self.assertEqual(load_lab_runtime({}, debug=False), LabRuntimeDisabled())
        self.assertEqual(
            load_lab_runtime({"TEXT_JUDGMENT_LAB_ENABLED": "false"}, debug=False),
            LabRuntimeDisabled(),
        )

    # テストケース: 有効化された正しい設定を読む。
    # 期待値: opaque IDとoriginを保持し、秘密値はreprへ露出しない。
    def test_loads_valid_enabled_runtime_without_exposing_secrets(self):
        runtime = load_lab_runtime(self.valid_environment(), debug=False)

        self.assertIsInstance(runtime, LabRuntimeConfigured)
        assert isinstance(runtime, LabRuntimeConfigured)
        self.assertEqual(runtime.channel_id, "0012345678")
        self.assertEqual(runtime.origin, "https://lab.example.test")
        self.assertEqual(runtime.model, "jev-1.13.0")
        self.assertNotIn("test-typesafe-secret", repr(runtime))
        self.assertTrue(runtime.owner_digest.matches("a" * 64))

    # テストケース: DEBUGまたは不足・不正設定でラボを有効化する。
    # 期待値: 例外でプロセスを止めず、秘密値を含まない固定理由で利用不可になる。
    def test_fails_closed_for_debug_missing_and_invalid_settings(self):
        debug_runtime = load_lab_runtime(self.valid_environment(), debug=True)
        missing_runtime = load_lab_runtime(
            {"TEXT_JUDGMENT_LAB_ENABLED": "true"}, debug=False
        )
        invalid_environment = self.valid_environment()
        invalid_environment["TEXT_JUDGMENT_LAB_ORIGIN"] = "http://lab.example.test/path"
        invalid_runtime = load_lab_runtime(invalid_environment, debug=False)

        self.assertEqual(debug_runtime, LabRuntimeUnavailable("debug_enabled"))
        self.assertEqual(missing_runtime, LabRuntimeUnavailable("invalid_configuration"))
        self.assertEqual(invalid_runtime, LabRuntimeUnavailable("invalid_configuration"))

    # テストケース: enabledや固定modelへ未知値を渡す。
    # 期待値: truthy化やmodel fallbackをせず利用不可になる。
    def test_rejects_ambiguous_enabled_and_model_values(self):
        ambiguous = self.valid_environment()
        ambiguous["TEXT_JUDGMENT_LAB_ENABLED"] = "TRUE"
        wrong_model = self.valid_environment()
        wrong_model["TEXT_JUDGMENT_LAB_MODEL"] = "jev-latest"

        self.assertEqual(
            load_lab_runtime(ambiguous, debug=False),
            LabRuntimeUnavailable("invalid_configuration"),
        )
        self.assertEqual(
            load_lab_runtime(wrong_model, debug=False),
            LabRuntimeUnavailable("invalid_configuration"),
        )
