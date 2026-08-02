from django.core import checks
from django.test import SimpleTestCase, override_settings

from linerichmenus.container import (
    LIFECYCLE_INTEGRATION_MARKER,
    build_mutation_readiness,
    validate_mutation_readiness_configuration,
)
from linerichmenus.types import IntegrationNotReady, MutationReady, OperationKind


class MutationReadinessTests(SimpleTestCase):
    # テストケース: foundation単独のread_only modeで全mutation種別を照合する。
    # 期待値: 全種別が外部作用前にintegration_not_readyへ分類される。
    def test_read_only_rejects_every_mutation_kind(self):
        readiness = build_mutation_readiness(mode="read_only")

        for kind in OperationKind:
            with self.subTest(kind=kind):
                self.assertEqual(
                    readiness.authorize(kind),
                    IntegrationNotReady(reason="integration_not_ready"),
                )

    # テストケース: 完全統合済みのrecovery_only modeでoperation種別を照合する。
    # 期待値: applyだけを拒否し、既存管理状態を解消する4種別を許可する。
    def test_recovery_only_allows_only_recovery_operations(self):
        readiness = self._build_integrated("recovery_only")

        self.assertEqual(
            readiness.authorize(OperationKind.APPLY),
            IntegrationNotReady(reason="integration_not_ready"),
        )
        for kind in (
            OperationKind.UNLINK,
            OperationKind.RELEASE,
            OperationKind.RECHECK,
            OperationKind.CLEANUP,
        ):
            with self.subTest(kind=kind):
                self.assertEqual(readiness.authorize(kind), MutationReady())

    # テストケース: 完全統合済みのenabled modeで全mutation種別を照合する。
    # 期待値: closed setに含まれる全種別が許可される。
    def test_enabled_allows_every_mutation_kind(self):
        readiness = self._build_integrated("enabled")

        for kind in OperationKind:
            with self.subTest(kind=kind):
                self.assertEqual(readiness.authorize(kind), MutationReady())

    # テストケース: elevated modeでprobe・purge・markerを一つずつ欠落させる。
    # 期待値: 各不完全構成が安全なintegration_not_readyへfail closedになる。
    def test_incomplete_integration_fails_closed(self):
        cases = (
            {"reference_probe_integrated": False},
            {"history_purge_integrated": False},
            {"integration_marker": ""},
        )
        for mode in ("recovery_only", "enabled"):
            for missing in cases:
                kwargs = {
                    "mode": mode,
                    "reference_probe_integrated": True,
                    "history_purge_integrated": True,
                    "integration_marker": LIFECYCLE_INTEGRATION_MARKER,
                    **missing,
                }
                with self.subTest(mode=mode, missing=missing):
                    readiness = build_mutation_readiness(**kwargs)
                    self.assertEqual(
                        readiness.authorize(OperationKind.UNLINK),
                        IntegrationNotReady(reason="integration_not_ready"),
                    )
                    self.assertFalse(readiness.configuration_valid)

    # テストケース: 未定義modeまたはoperation kindを境界へ渡す。
    # 期待値: fallbackや例外露出をせず安全な拒否結果になる。
    def test_unknown_mode_or_operation_fails_closed(self):
        readiness = build_mutation_readiness(mode="unexpected")

        self.assertEqual(
            readiness.authorize(OperationKind.APPLY),
            IntegrationNotReady(reason="integration_not_ready"),
        )
        self.assertEqual(
            self._build_integrated("enabled").authorize("unexpected"),
            IntegrationNotReady(reason="unsupported_operation"),
        )

    # テストケース: elevated modeと不完全な統合設定をstartup checkへ渡す。
    # 期待値: Django checkが安全な固定エラーで起動をfail closedにする。
    @override_settings(
        LINE_RICH_MENU_MUTATION_MODE="enabled",
        LINE_RICH_MENU_REFERENCE_PROBE_INTEGRATED=False,
        LINE_RICH_MENU_HISTORY_PURGE_INTEGRATED=True,
        LINE_RICH_MENU_INTEGRATION_MARKER=LIFECYCLE_INTEGRATION_MARKER,
    )
    def test_startup_check_rejects_incomplete_elevated_configuration(self):
        errors = checks.run_checks()

        self.assertIn("linerichmenus.E010", {error.id for error in errors})
        self.assertNotIn(LIFECYCLE_INTEGRATION_MARKER, repr(errors))

    # テストケース: foundation既定のread_only構成をstartup checkへ渡す。
    # 期待値: 統合markerなしでも安全なread-only起動が許可される。
    @override_settings(
        LINE_RICH_MENU_MUTATION_MODE="read_only",
        LINE_RICH_MENU_REFERENCE_PROBE_INTEGRATED=False,
        LINE_RICH_MENU_HISTORY_PURGE_INTEGRATED=False,
        LINE_RICH_MENU_INTEGRATION_MARKER="",
    )
    def test_startup_check_accepts_foundation_read_only_configuration(self):
        self.assertEqual(validate_mutation_readiness_configuration(), ())

    def _build_integrated(self, mode):
        return build_mutation_readiness(
            mode=mode,
            reference_probe_integrated=True,
            history_purge_integrated=True,
            integration_marker=LIFECYCLE_INTEGRATION_MARKER,
        )
