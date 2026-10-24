from unittest.mock import patch
from uuid import uuid4

from django.test import TransactionTestCase

from lineaccounts.admin_authorization import OwnerOperationContext
from linerichmenus.gateway import (
    CreateAccepted,
    GatewayAccepted,
    GatewayUnknown,
    ResourceAbsent,
    ResourceObserved,
    ResourceSummary,
    RichMenuDefaultNone,
    RichMenuDefaultPresent,
    RichMenuDefaultUnknown,
)
from linerichmenus.models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation
from linerichmenus.repository import DjangoRichMenuRepository, StageConflict
from linerichmenus.services import (
    DefaultMutationReadiness,
    DefaultRichMenuService,
    OperationSucceeded,
)
from linerichmenus.types import (
    ConfirmationAccepted,
    IssuedConfirmation,
    NextAllowedAction,
    OperationCommand,
    OperationKind,
    OperationStage,
    OperationStatus,
)

from .test_repository_acceptance import LockedFence
from .test_repository_stage_cas import ExactOperationFence
from .test_services import (
    NOW,
    FixedConfirmation,
    RecordingChannelPort,
    RecordingGateway,
    RecordingOwnerFence,
    _input,
    _snapshot,
)


class StatefulRichMenuGateway(RecordingGateway):
    def __init__(self):
        super().__init__(RichMenuDefaultNone())
        self.resources = {}
        self.unknown_setting = False
        self.unknown_verifying = False
        self.after_setting = False

    def create(self, context, request):
        self.calls.append(("create", context, request))
        line_id = "rich-menu-" + uuid4().hex
        self.resources[line_id] = ResourceSummary(line_id, request.name)
        return CreateAccepted(line_id)

    def set_default(self, context, rich_menu_id):
        result = super().set_default(context, rich_menu_id)
        self.after_setting = True
        return GatewayUnknown("timeout_unknown") if self.unknown_setting else result

    def get_default(self, context):
        if self.after_setting and self.unknown_verifying:
            self.calls.append(("get_default", context))
            self.unknown_verifying = False
            return RichMenuDefaultUnknown("response_unknown")
        return super().get_default(context)

    def get_resource(self, context, rich_menu_id):
        self.calls.append(("get_resource", context, rich_menu_id))
        resource = self.resources.get(rich_menu_id)
        return ResourceAbsent() if resource is None else ResourceObserved(resource)

    def delete(self, context, rich_menu_id):
        self.calls.append(("delete", context, rich_menu_id))
        self.resources.pop(rich_menu_id, None)
        return GatewayAccepted()


class RichMenuApplyCleanupIntegrationTests(TransactionTestCase):
    def setUp(self):
        self.identity_id = uuid4()
        self.channel_id = uuid4()
        self.owner = OwnerOperationContext(uuid4(), self.identity_id)
        self.repository = DjangoRichMenuRepository(
            reference_fence=LockedFence(), operation_fence=ExactOperationFence(), clock=lambda: NOW
        )
        self.gateway = StatefulRichMenuGateway()
        self.confirmation = FixedConfirmation(
            IssuedConfirmation(token="confirmation-token", expires_at=NOW, usage_digest="b" * 64)
        )
        self.service = DefaultRichMenuService(
            owner_fence=RecordingOwnerFence(identity_id=self.identity_id),
            channel_port=RecordingChannelPort(_snapshot(self.channel_id, self.identity_id)),
            repository=self.repository,
            gateway=self.gateway,
            confirmation=self.confirmation,
            readiness=DefaultMutationReadiness(mode="enabled", integration_complete=True),
            clock=lambda: NOW,
        )

    def _apply(self):
        self.confirmation.verify_result = ConfirmationAccepted(uuid4().hex * 2)
        self.gateway.after_setting = False
        command = OperationCommand(
            operation_id=uuid4(),
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
            kind=OperationKind.APPLY,
            subject_operation_id=None,
            target_resource_id=None,
            confirmation_token="confirmation-token",
            template=_input(),
        )
        result = self.service.start_operation(self.owner, command)
        self.assertIsInstance(result, OperationSucceeded)
        return result.operation

    def _recheck(self, operation):
        result = self.service.start_operation(
            self.owner,
            OperationCommand(
                operation_id=uuid4(),
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
                kind=OperationKind.RECHECK,
                subject_operation_id=operation.operation_id,
                target_resource_id=None,
            ),
        )
        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.SUCCEEDED)
        return self.repository.get_operation_by_id(operation.operation_id)

    def _assert_cleanup_and_finish(self, replacement, old):
        self.assertEqual(replacement.status, OperationStatus.CLEANUP_REQUIRED)
        self.assertEqual(replacement.stage, OperationStage.CLEANING)
        self.assertIn(NextAllowedAction.CLEANUP, replacement.next_allowed_actions)
        old.refresh_from_db()
        state = RichMenuChannelState.objects.get(pk=self.channel_id)
        self.assertEqual(old.lifecycle, "old")
        self.assertEqual(old.replacement_operation_id, replacement.operation_id)
        self.assertEqual(state.blocking_operation_id, replacement.operation_id)
        self.assertIsNone(state.active_operation_id)
        current_id = state.current_resource_id
        self.assertNotEqual(current_id, old.public_id)
        current = ManagedRichMenu.objects.get(pk=current_id)
        self.assertEqual(current.lifecycle, "applied")
        self.assertEqual(self.gateway.default, RichMenuDefaultPresent(current.line_rich_menu_id))
        result = self.service.start_operation(
            self.owner,
            OperationCommand(
                operation_id=uuid4(),
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
                kind=OperationKind.CLEANUP,
                subject_operation_id=replacement.operation_id,
                target_resource_id=old.public_id,
            ),
        )
        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.SUCCEEDED)
        old.refresh_from_db()
        state.refresh_from_db()
        self.assertEqual(old.lifecycle, "deleted")
        self.assertEqual(state.current_resource_id, current_id)
        self.assertIsNone(state.blocking_operation_id)
        self.assertIsNone(state.active_operation_id)
        self.assertEqual(
            [call[2] for call in self.gateway.calls if call[0] == "delete"],
            [old.line_rich_menu_id],
        )
        self.assertEqual(
            RichMenuOperation.objects.get(pk=replacement.operation_id).status, "succeeded"
        )
        self.assertEqual(self._apply().status, OperationStatus.CLEANUP_REQUIRED)

    # テストケース: 初回適用後に別のメニューへ置換し、旧資源の後片付けを明示実行する。
    # 期待値: 初回だけ成功し、置換はblockerとcleanup操作を維持して旧資源削除後に解放する。
    def test_first_apply_then_replacement_allows_existing_cleanup_to_finish(self):
        first = self._apply()
        self.assertEqual(first.status, OperationStatus.SUCCEEDED)
        old = ManagedRichMenu.objects.get(origin_operation_id=first.operation_id)
        self._assert_cleanup_and_finish(self._apply(), old)

    # テストケース: 置換の既定設定応答が不明になり、明示再確認で新既定を確認する。
    # 期待値: 既定設定を再送せず新旧資源を確定し、旧資源を既存cleanupで削除できる。
    def test_unknown_setting_recheck_converges_to_replacement_cleanup(self):
        first = self._apply()
        old = ManagedRichMenu.objects.get(origin_operation_id=first.operation_id)
        self.gateway.unknown_setting = True
        unknown = self._apply()
        self.assertEqual(unknown.status, OperationStatus.UNKNOWN)
        self.assertEqual(unknown.stage, OperationStage.SETTING_DEFAULT)
        replacement = self._recheck(unknown)
        self.assertEqual(len([call for call in self.gateway.calls if call[0] == "set_default"]), 2)
        self.gateway.unknown_setting = False
        self._assert_cleanup_and_finish(replacement, old)

    # テストケース: 置換の既定確認が不明になり、明示再確認で新既定を確認する。
    # 期待値: 成功としてblockerを解放せず、新currentと旧資源cleanupを両立する。
    def test_unknown_verifying_recheck_converges_to_replacement_cleanup(self):
        first = self._apply()
        old = ManagedRichMenu.objects.get(origin_operation_id=first.operation_id)
        self.gateway.unknown_verifying = True
        unknown = self._apply()
        self.assertEqual(unknown.status, OperationStatus.UNKNOWN)
        self.assertEqual(unknown.stage, OperationStage.VERIFYING)
        replacement = self._recheck(unknown)
        self._assert_cleanup_and_finish(replacement, old)

    # テストケース: 初回適用の既定設定が結果不明になった後、明示再確認する。
    # 期待値: 旧資源を作らず成功・currentへ確定し、blockerとactiveを解放する。
    def test_first_apply_unknown_setting_recheck_succeeds_without_cleanup(self):
        self.gateway.unknown_setting = True
        unknown = self._apply()
        resolved = self._recheck(unknown)
        self.assertEqual(resolved.status, OperationStatus.SUCCEEDED)
        state = RichMenuChannelState.objects.get(pk=self.channel_id)
        self.assertEqual(state.current_resource.origin_operation_id, unknown.operation_id)
        self.assertEqual(state.current_resource.lifecycle, "applied")
        self.assertIsNone(state.blocking_operation_id)
        self.assertIsNone(state.active_operation_id)

    # テストケース: 置換の資源確定後にstage完了の競合が起きる。
    # 期待値: 資源とrelationの部分commitを残さず、元currentを保って明示再確認可能にする。
    def test_replacement_stage_conflict_rolls_back_resource_finalization(self):
        first = self._apply()
        old = ManagedRichMenu.objects.get(origin_operation_id=first.operation_id)
        complete_stage = self.repository.complete_stage

        def fail_cleanup_completion(outcome):
            if outcome.next_status is OperationStatus.CLEANUP_REQUIRED:
                return StageConflict("stale_stage")
            return complete_stage(outcome)

        with patch.object(self.repository, "complete_stage", side_effect=fail_cleanup_completion):
            replacement = self._apply()
        self.assertEqual(replacement.status, OperationStatus.UNKNOWN)
        old.refresh_from_db()
        self.assertEqual(old.lifecycle, "applied")
        self.assertIsNone(old.replacement_operation_id)
        state = RichMenuChannelState.objects.get(pk=self.channel_id)
        self.assertEqual(state.current_resource_id, old.public_id)
        self.assertEqual(state.blocking_operation_id, replacement.operation_id)
        resolved = self._recheck(replacement)
        self._assert_cleanup_and_finish(resolved, old)
