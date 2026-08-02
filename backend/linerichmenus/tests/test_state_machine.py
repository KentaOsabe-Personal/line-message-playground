from django.test import SimpleTestCase

from linerichmenus.state_machine import (
    InvalidStateTransition,
    transition_operation,
    transition_resource,
)
from linerichmenus.types import OperationKind, OperationStage, OperationStatus, ResourceLifecycle


class RichMenuStateMachineTests(SimpleTestCase):
    # テストケース: operationの許可済み状態・stage遷移をすべて評価する。
    # 期待値: 各入力は設計で定めた一意な次状態へ遷移する。
    def test_operation_transitions_are_deterministic(self):
        cases = (
            (OperationKind.APPLY, OperationStatus.ACCEPTED, None, OperationStatus.PROCESSING, OperationStage.CREATING),
            (OperationKind.APPLY, OperationStatus.PROCESSING, OperationStage.CREATING, OperationStatus.PROCESSING, OperationStage.UPLOADING),
            (OperationKind.APPLY, OperationStatus.PROCESSING, OperationStage.UPLOADING, OperationStatus.CLEANUP_REQUIRED, OperationStage.CLEANING),
            (OperationKind.APPLY, OperationStatus.PROCESSING, OperationStage.SETTING_DEFAULT, OperationStatus.UNKNOWN, OperationStage.SETTING_DEFAULT),
            (OperationKind.RECHECK, OperationStatus.RECOVERY_ACTIVE, OperationStage.VERIFYING, OperationStatus.SUCCEEDED, OperationStage.VERIFYING),
            (OperationKind.CLEANUP, OperationStatus.RECOVERY_ACTIVE, OperationStage.CLEANING, OperationStatus.UNKNOWN, OperationStage.CLEANING),
        )
        for kind, current_status, current_stage, next_status, next_stage in cases:
            with self.subTest(current_status=current_status, next_status=next_status):
                result = transition_operation(
                    kind=kind,
                    current_status=current_status,
                    current_stage=current_stage,
                    next_status=next_status,
                    next_stage=next_stage,
                )
                self.assertEqual(result.status, next_status)
                self.assertEqual(result.stage, next_stage)

    # テストケース: terminal状態、不正stage、飛び越し遷移を要求する。
    # 期待値: 状態変更を表す結果を返さず安全に拒否する。
    def test_operation_transitions_reject_invalid_edges(self):
        cases = (
            (OperationKind.APPLY, OperationStatus.SUCCEEDED, OperationStage.VERIFYING, OperationStatus.PROCESSING, OperationStage.CREATING),
            (OperationKind.APPLY, OperationStatus.FAILED, OperationStage.CREATING, OperationStatus.PROCESSING, OperationStage.UPLOADING),
            (OperationKind.APPLY, OperationStatus.ACCEPTED, None, OperationStatus.PROCESSING, OperationStage.UPLOADING),
            (OperationKind.APPLY, OperationStatus.PROCESSING, OperationStage.CREATING, OperationStatus.PROCESSING, OperationStage.SETTING_DEFAULT),
            (OperationKind.APPLY, OperationStatus.PROCESSING, OperationStage.CREATING, OperationStatus.SUCCEEDED, OperationStage.LOCAL_RELEASE),
            (OperationKind.APPLY, OperationStatus.PROCESSING, OperationStage.CREATING, OperationStatus.SUCCEEDED, OperationStage.CREATING),
            (OperationKind.APPLY, OperationStatus.PROCESSING, OperationStage.CREATING, OperationStatus.CLEANUP_REQUIRED, OperationStage.CLEANING),
            (OperationKind.RECHECK, OperationStatus.RECOVERY_ACTIVE, OperationStage.VERIFYING, OperationStatus.PROCESSING, OperationStage.CREATING),
            (OperationKind.RELEASE, OperationStatus.ACCEPTED, None, OperationStatus.PROCESSING, OperationStage.CREATING),
        )
        for kind, current_status, current_stage, next_status, next_stage in cases:
            with self.subTest(current_status=current_status, next_status=next_status), self.assertRaises(InvalidStateTransition):
                transition_operation(
                    kind=kind,
                    current_status=current_status,
                    current_stage=current_stage,
                    next_status=next_status,
                    next_stage=next_stage,
                )

    # テストケース: 管理資源のcandidateからterminalまでの許可遷移を評価する。
    # 期待値: candidate→applied→old/cleanup→deletedとapplied→releasedだけが許可される。
    def test_resource_lifecycle_transitions(self):
        allowed = (
            (ResourceLifecycle.CANDIDATE, ResourceLifecycle.APPLIED),
            (ResourceLifecycle.CANDIDATE, ResourceLifecycle.CLEANUP_REQUIRED),
            (ResourceLifecycle.APPLIED, ResourceLifecycle.OLD),
            (ResourceLifecycle.APPLIED, ResourceLifecycle.CLEANUP_REQUIRED),
            (ResourceLifecycle.APPLIED, ResourceLifecycle.RELEASED),
            (ResourceLifecycle.OLD, ResourceLifecycle.CLEANUP_REQUIRED),
            (ResourceLifecycle.OLD, ResourceLifecycle.DELETED),
            (ResourceLifecycle.CLEANUP_REQUIRED, ResourceLifecycle.DELETED),
        )
        for current, next_lifecycle in allowed:
            with self.subTest(current=current, next_lifecycle=next_lifecycle):
                self.assertEqual(transition_resource(current, next_lifecycle), next_lifecycle)

        rejected = (
            (ResourceLifecycle.CANDIDATE, ResourceLifecycle.DELETED),
            (ResourceLifecycle.APPLIED, ResourceLifecycle.DELETED),
            (ResourceLifecycle.RELEASED, ResourceLifecycle.APPLIED),
            (ResourceLifecycle.DELETED, ResourceLifecycle.CANDIDATE),
        )
        for current, next_lifecycle in rejected:
            with self.subTest(current=current, next_lifecycle=next_lifecycle), self.assertRaises(InvalidStateTransition):
                transition_resource(current, next_lifecycle)

    # テストケース: unlinkとreleaseに対応するresource遷移を比較する。
    # 期待値: unlinkはcleanup_required、releaseはreleasedとなり同一操作として扱われない。
    def test_unlink_and_release_are_distinct(self):
        self.assertEqual(
            transition_resource(ResourceLifecycle.APPLIED, ResourceLifecycle.CLEANUP_REQUIRED),
            ResourceLifecycle.CLEANUP_REQUIRED,
        )
        self.assertEqual(
            transition_resource(ResourceLifecycle.APPLIED, ResourceLifecycle.RELEASED),
            ResourceLifecycle.RELEASED,
        )
