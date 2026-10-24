from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction

from lineaccounts.models import OwnerAccount
from lineaccounts.repositories import DjangoAccountRepository
from linechannels.admin_lifecycle_repositories import DjangoChannelDeactivationRepository
from linechannels.models import LineChannel
from linerichmenus.cleanup_repair import CleanupRepairRejected, CleanupStateRepair
from linerichmenus.gateway import ResourceSummary, RichMenuDefaultPresent
from linerichmenus.models import (
    ManagedRichMenu,
    RichMenuChannelState,
    RichMenuOperation,
    RichMenuOperationTransition,
)
from linerichmenus.repository import DjangoRichMenuRepository
from linerichmenus.services import (
    DefaultMutationReadiness,
    DefaultRichMenuService,
    OperationSucceeded,
)
from linerichmenus.types import OperationCommand, OperationKind, OperationStatus

from .test_apply_cleanup_integration import StatefulRichMenuGateway
from .test_current_concurrency import CurrentRealPortsFixture
from .test_current_service import NOW
from .test_repository_acceptance import LockedFence
from .test_repository_stage_cas import ExactOperationFence


class CleanupRepairTests(CurrentRealPortsFixture):
    def setUp(self):
        super().setUp()
        previous = self.operation(
            owner_identity_public_id=self.identity.public_id, accepted_at=NOW - timedelta(days=1)
        )
        self.old = self.make_resource(previous, lifecycle="old", replacement_operation=self.origin)
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(completed_at=NOW)
        self.original_transition = RichMenuOperationTransition.objects.create(
            operation=self.origin,
            sequence=1,
            from_status="processing",
            to_status="succeeded",
            stage="verifying",
            safe_reason="succeeded",
            observed_at=NOW,
        )
        self.repair = CleanupStateRepair(
            accounts=DjangoAccountRepository(),
            channels=self.service._channel_port,
            deactivation=DjangoChannelDeactivationRepository(),
            clock=lambda: NOW + timedelta(hours=1),
        )

    def apply(self):
        return self.repair.run(
            self.scope.channel_public_id,
            NOW,
            apply=True,
            subject_id=self.origin.pk,
            target_id=self.old.pk,
        )

    # テストケース: 置換成功扱いで残った旧資源をdry-runする。
    # 期待値: 一件の操作と資源だけを提示し、履歴・blocker・LINEへ変更しない。
    def test_dry_run_does_not_change_state_or_history(self):
        result = self.repair.run(self.scope.channel_public_id, NOW)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.subject_operation_id, self.origin.pk)
        self.assertEqual(result.target_resource_id, self.old.pk)
        self.origin.refresh_from_db()
        self.state.refresh_from_db()
        self.assertEqual(self.origin.status, "succeeded")
        self.assertIsNone(self.state.blocking_operation_id)
        self.assertEqual(self.origin.transitions.count(), 1)
        self.assertEqual(self.gateway.mock_calls, [])

    # テストケース: dry-runの一組を明示指定して修復し、同じ要求を再実行する。
    # 期待値: 元成功記録・設定・完了日時を残して修復を一回追記し、現在資源を変更しない。
    def test_apply_preserves_success_history_and_is_idempotent(self):
        before = self.origin.configuration_snapshot
        result = self.apply()
        self.assertEqual(result.status, "repaired")
        self.origin.refresh_from_db()
        self.state.refresh_from_db()
        self.old.refresh_from_db()
        self.assertEqual(self.origin.status, "cleanup_required")
        self.assertEqual(self.origin.stage, "cleaning")
        self.assertEqual(self.origin.configuration_snapshot, before)
        self.assertEqual(self.origin.completed_at, NOW)
        self.assertEqual(self.state.blocking_operation_id, self.origin.pk)
        self.assertEqual(self.state.current_resource_id, self.resource.pk)
        self.assertEqual(self.old.lifecycle, "old")
        self.original_transition.refresh_from_db()
        self.assertEqual(self.original_transition.to_status, "succeeded")
        correction = self.origin.transitions.get(sequence=2)
        self.assertEqual(
            (correction.from_status, correction.to_status), ("succeeded", "cleanup_required")
        )
        self.assertEqual(self.apply().status, "already_repaired")
        self.assertEqual(self.origin.transitions.count(), 2)
        self.assertEqual(self.gateway.mock_calls, [])

    # テストケース: 修復後にcurrentサービスの回復候補を取得する。
    # 期待値: 既存cleanupに渡せる同じsubjectと旧資源の一組だけを返す。
    def test_repaired_subject_is_available_to_existing_current_projection(self):
        self.apply()
        value = self.read()
        self.assertEqual(value.current.kind, "managed")
        self.assertEqual(len(value.recovery_targets), 1)
        target = value.recovery_targets[0]
        self.assertEqual(target.subject_operation_id, self.origin.pk)
        self.assertEqual(target.target_resource_id, self.old.pk)
        self.assertEqual(
            {action.value for action in value.rich.capabilities.actions},
            {"cleanup", "get_state", "view_history"},
        )

    # テストケース: 不明な置換・別owner・結果不明・現在既定のIDを修復対象へ混ぜる。
    # 期待値: 根拠を推測せず拒否し、元の履歴とblockerを保持する。
    def test_rejects_unproven_or_unsafe_resource_relationships(self):
        changes = (
            lambda: ManagedRichMenu.objects.filter(pk=self.old.pk).update(
                replacement_operation=None
            ),
            lambda: RichMenuOperation.objects.filter(pk=self.origin.pk).update(
                owner_identity_public_id=uuid4()
            ),
            lambda: RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="unknown"),
            lambda: RichMenuChannelState.objects.filter(pk=self.state.pk).update(
                current_resource=self.old
            ),
            lambda: ManagedRichMenu.objects.filter(pk=self.old.pk).update(
                lifecycle="released", released_at=NOW, replacement_operation=None
            ),
        )
        for change in changes:
            with self.subTest(change=changes.index(change)), transaction.atomic():
                change()
                with self.assertRaises(CleanupRepairRejected):
                    self.apply()
                self.assertEqual(self.origin.transitions.count(), 1)
                transaction.set_rollback(True)

    # テストケース: owner失効・channel revision変更・停止中・別操作受付中に修復する。
    # 期待値: 通常writerと同じfenceで拒否して修復記録を追加しない。
    def test_rejects_authorization_revision_and_active_operation_changes(self):
        for change in (
            lambda: OwnerAccount.objects.filter(slot=1).update(
                state="deauthorization_pending", unlink_generation=uuid4()
            ),
            lambda: LineChannel.objects.filter(pk=self.real_channel.pk).update(
                updated_at=NOW + timedelta(seconds=1)
            ),
            lambda: LineChannel.objects.filter(pk=self.real_channel.pk).update(is_active=False),
            lambda: RichMenuChannelState.objects.filter(pk=self.state.pk).update(
                active_operation=self.origin
            ),
        ):
            with self.subTest(change=change.__name__), transaction.atomic():
                change()
                with self.assertRaises(CleanupRepairRejected):
                    self.apply()
                self.assertEqual(self.origin.transitions.count(), 1)
                transaction.set_rollback(True)

    # テストケース: 確認したsubject/targetを省略または別の値にしてapplyする。
    # 期待値: 暗黙に別対象を修復せず、確認不足として拒否する。
    def test_requires_an_exact_confirmed_pair(self):
        with self.assertRaises(CleanupRepairRejected):
            self.repair.run(self.scope.channel_public_id, NOW, apply=True)
        with self.assertRaises(CleanupRepairRejected):
            self.repair.run(
                self.scope.channel_public_id,
                NOW,
                apply=True,
                subject_id=uuid4(),
                target_id=self.old.pk,
            )

    # テストケース: コマンドをdry-runし、確認値なしapplyと不正日時を渡す。
    # 期待値: UUIDと件数以外の秘密を出力せず、安全なエラーで拒否する。
    def test_management_command_is_dry_run_by_default_and_rejects_invalid_apply(self):
        with patch(
            "linerichmenus.management.commands.repair_rich_menu_cleanup.build_repair_service",
            return_value=self.repair,
        ):
            output = StringIO()
            call_command(
                "repair_rich_menu_cleanup",
                channel_id=self.scope.channel_public_id,
                expected_revision=NOW.isoformat(),
                stdout=output,
            )
            self.assertIn('"status": "dry_run"', output.getvalue())
            self.assertNotIn("test-token", output.getvalue())
            with self.assertRaises(CommandError):
                call_command(
                    "repair_rich_menu_cleanup",
                    channel_id=self.scope.channel_public_id,
                    expected_revision=NOW.isoformat(),
                    apply=True,
                )
            with self.assertRaises(CommandError):
                call_command(
                    "repair_rich_menu_cleanup",
                    channel_id=self.scope.channel_public_id,
                    expected_revision="invalid",
                )

    # テストケース: A→B→C→D(current)の旧資源3件を修復と既存cleanupで順に解消する。
    # 期待値: 先に消すべき前任がある対象を拒否し、現在Dと全履歴を維持して3件を削除する。
    def test_repairs_and_cleans_three_replacements_in_causal_order(self):
        origin_b = self.operation(
            owner_identity_public_id=self.identity.public_id, accepted_at=NOW - timedelta(days=2)
        )
        middle = self.make_resource(
            origin_b, lifecycle="old", replacement_operation=self.old.origin_operation
        )
        origin_a = self.operation(
            owner_identity_public_id=self.identity.public_id, accepted_at=NOW - timedelta(days=3)
        )
        oldest = self.make_resource(origin_a, lifecycle="old", replacement_operation=origin_b)
        with self.assertRaises(CleanupRepairRejected):
            self.apply()
        gateway = StatefulRichMenuGateway()
        gateway.default = RichMenuDefaultPresent(self.resource.line_rich_menu_id)
        for resource in (oldest, middle, self.old, self.resource):
            gateway.resources[resource.line_rich_menu_id] = ResourceSummary(
                resource.line_rich_menu_id, resource.ownership_marker
            )
        service = DefaultRichMenuService(
            owner_fence=self.service._owner_fence,
            channel_port=self.service._channel_port,
            repository=DjangoRichMenuRepository(
                reference_fence=LockedFence(),
                operation_fence=ExactOperationFence(),
                clock=lambda: NOW,
            ),
            gateway=gateway,
            readiness=DefaultMutationReadiness(mode="enabled", integration_complete=True),
            clock=lambda: NOW,
        )
        for resource in (oldest, middle, self.old):
            plan = self.repair.run(self.scope.channel_public_id, NOW)
            self.assertEqual(plan.target_resource_id, resource.pk)
            self.repair.run(
                self.scope.channel_public_id,
                NOW,
                apply=True,
                subject_id=plan.subject_operation_id,
                target_id=plan.target_resource_id,
            )
            result = service.start_operation(
                self.owner,
                OperationCommand(
                    operation_id=uuid4(),
                    channel_public_id=self.scope.channel_public_id,
                    expected_channel_revision=NOW,
                    kind=OperationKind.CLEANUP,
                    subject_operation_id=plan.subject_operation_id,
                    target_resource_id=plan.target_resource_id,
                ),
            )
            self.assertIsInstance(result, OperationSucceeded)
            self.assertEqual(result.operation.status, OperationStatus.SUCCEEDED)
            resource.refresh_from_db()
            self.state.refresh_from_db()
            self.assertEqual(resource.lifecycle, "deleted")
            self.assertEqual(self.state.current_resource_id, self.resource.pk)
            self.assertIsNone(self.state.blocking_operation_id)
        self.assertEqual(
            [call[2] for call in gateway.calls if call[0] == "delete"],
            [oldest.line_rich_menu_id, middle.line_rich_menu_id, self.old.line_rich_menu_id],
        )
        self.assertFalse(
            ManagedRichMenu.objects.filter(channel_state=self.state, lifecycle="old").exists()
        )
        self.assertEqual(gateway.default, RichMenuDefaultPresent(self.resource.line_rich_menu_id))
