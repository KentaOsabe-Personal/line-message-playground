from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from django.core import checks
from django.test import SimpleTestCase, override_settings

from linerichmenus.container import (
    LIFECYCLE_INTEGRATION_MARKER,
    build_headless_lifecycle_port,
    build_headless_reference_contracts,
    build_mutation_readiness,
    build_rich_menu_service,
    validate_mutation_readiness_configuration,
)
from linerichmenus.headless import DefaultRichMenuLifecyclePort, DjangoHeadlessReferenceContracts
from linerichmenus.headless import HeadlessCommand
from lineaccounts.admin_authorization import OwnerOperationContext
from linerichmenus.services import DefaultRichMenuService, ServiceFailed
from linerichmenus.types import (
    IntegrationNotReady,
    MutationReady,
    OperationCommand,
    OperationKind,
    SafeResultCode,
)


class MutationReadinessTests(SimpleTestCase):
    # テストケース: runtime composition rootからowner APIとheadless向けconcrete依存を構築する。
    # 期待値: service・lifecycle・reference/purgeが同じfail-closed設定で実体化される。
    def test_composition_root_builds_all_public_contracts(self):
        service = build_rich_menu_service()
        lifecycle = build_headless_lifecycle_port()
        references = build_headless_reference_contracts()

        self.assertIsInstance(service, DefaultRichMenuService)
        self.assertIsInstance(lifecycle, DefaultRichMenuLifecyclePort)
        self.assertIsInstance(lifecycle._service, DefaultRichMenuService)
        self.assertIsInstance(references, DjangoHeadlessReferenceContracts)

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

    # テストケース: integration marker欠落構成でowner入口とheadless入口から同じunlinkを開始する。
    # 期待値: 両入口がintegration_not_readyへ一致し、fence・repository・LINEを一件も呼ばない。
    def test_owner_and_headless_mutations_share_fail_closed_composition(self):
        gateway = Mock()
        owner_fence = Mock()
        channel_port = Mock()
        repository = Mock()
        service = DefaultRichMenuService(
            owner_fence=owner_fence,
            channel_port=channel_port,
            repository=repository,
            gateway=gateway,
            readiness=build_mutation_readiness(
                mode="enabled",
                reference_probe_integrated=True,
                history_purge_integrated=True,
                integration_marker="",
            ),
        )
        owner = OwnerOperationContext(uuid4(), uuid4())
        command = OperationCommand(
            operation_id=uuid4(),
            channel_public_id=uuid4(),
            expected_channel_revision=datetime(2026, 8, 2, tzinfo=UTC),
            kind=OperationKind.UNLINK,
            subject_operation_id=None,
            target_resource_id=uuid4(),
        )

        owner_result = service.start_operation(owner, command)
        headless_result = DefaultRichMenuLifecyclePort(service).start_unlink(
            HeadlessCommand(
                owner=owner,
                channel_public_id=command.channel_public_id,
                expected_channel_revision=command.expected_channel_revision,
                operation=command,
            )
        )

        for result in (owner_result, headless_result):
            self.assertIsInstance(result, ServiceFailed)
            self.assertEqual(result.code, SafeResultCode.INTEGRATION_NOT_READY)
        owner_fence.lock_active.assert_not_called()
        channel_port.snapshot_exact.assert_not_called()
        repository.accept.assert_not_called()
        self.assertEqual(gateway.mock_calls, [])

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
