from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from django.test import TransactionTestCase

from lineaccounts.admin_authorization import OwnerActiveProof, OwnerOperationContext
from linechannels.admin_types import (
    ExactChannelSnapshotAvailable,
    RichMenuChannelSnapshot,
)
from linechannels.types import AccessToken
from linerichmenus.gateway import (
    CreateAccepted,
    GatewayAccepted,
    GatewayRejected,
    GatewayUnknown,
    RichMenuDefaultExternal,
    RichMenuDefaultNone,
    RichMenuDefaultPresent,
    RichMenuDefaultUnknown,
)
from linerichmenus.reconciliation import (
    DefaultRichMenuReconciler,
    ManagedResourceTarget,
    RecheckConfirmed,
    RecheckUnknown,
    Reconciliation,
)
from linerichmenus.renderer import DefaultDeterministicRenderer
from linerichmenus.repository import (
    HistoryQuery,
    OperationAccepted,
    OperationFenceSnapshot,
    OwnerChannelScope,
    RecoveryAccepted,
    RecoveryHandoffResult,
    StageClaimed,
)
from linerichmenus.services import (
    DefaultMutationReadiness,
    DefaultRichMenuService,
    HistorySucceeded,
    OperationSucceeded,
    PreviewRequest,
    PreviewSucceeded,
    ServiceFailed,
    StateSucceeded,
)
from linerichmenus.types import (
    ChannelStateView,
    ConfirmationAccepted,
    ConfirmationRejected,
    DefaultObservation,
    HistoryPage,
    HistorySummary,
    InputFieldError,
    NextAllowedAction,
    ObservationKind,
    OperationKind,
    OperationCommand,
    OperationStage,
    OperationStatus,
    OperationView,
    RenderRejected,
    ResourceLifecycle,
    SafeResultCode,
    IssuedConfirmation,
    TemplateInput,
    TemplateReference,
)


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class RecordingOwnerFence:
    def __init__(self, *, identity_id, provider_id="provider-1"):
        self.identity_id = identity_id
        self.provider_id = provider_id
        self.calls = []

    def lock_active(self, context, now):
        self.calls.append((context, now))
        return OwnerActiveProof(self.identity_id, self.provider_id)


class RecordingChannelPort:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.commands = []

    def snapshot_exact(self, command):
        self.commands.append(command)
        return ExactChannelSnapshotAvailable(self.snapshot)

    def snapshot_latest(self, channel_public_id, owner_identity_public_id, provider_id):
        del owner_identity_public_id, provider_id
        self.commands.append(channel_public_id)
        return ExactChannelSnapshotAvailable(self.snapshot)


class RecordingGateway:
    def __init__(self, default):
        self.default = default
        self.default_sequence = []
        self.create_result = CreateAccepted("rich-menu-created")
        self.upload_result = GatewayAccepted()
        self.calls = []

    def validate(self, context, request):
        self.calls.append(("validate", context, request))
        return GatewayAccepted()

    def get_default(self, context):
        self.calls.append(("get_default", context))
        if self.default_sequence:
            self.default = self.default_sequence.pop(0)
        return self.default

    def create(self, context, request):
        self.calls.append(("create", context, request))
        return self.create_result

    def upload(self, context, rich_menu_id, image):
        self.calls.append(("upload", context, rich_menu_id, image))
        return self.upload_result

    def set_default(self, context, rich_menu_id):
        self.calls.append(("set_default", context, rich_menu_id))
        self.default = RichMenuDefaultPresent(rich_menu_id)
        return GatewayAccepted()

    def clear_default(self, context):
        self.calls.append(("clear_default", context))
        self.default = RichMenuDefaultExternal()
        return GatewayAccepted()

    def delete(self, context, rich_menu_id):
        self.calls.append(("delete", context, rich_menu_id))
        return GatewayAccepted()


class RecordingReconciler:
    def __init__(self, reconciliation):
        self.reconciliation = reconciliation
        self.recheck_result = RecheckUnknown(
            OperationStage.VERIFYING, "observation_unknown"
        )
        self.calls = []
        self.recheck_calls = []

    def observe_channel(self, context):
        self.calls.append(context)
        return self.reconciliation

    def recheck_operation(self, context):
        self.recheck_calls.append(context)
        return self.recheck_result


class RecordingRepository:
    def __init__(self):
        self.observations = []
        self.state = None
        self.operation = None
        self.history = HistoryPage(entries=(), next_cursor=None, has_more=False)

    def list_managed_resources(self, scope):
        return ()

    def record_observation(self, scope, observation):
        self.observations.append((scope, observation))
        if self.state is not None:
            self.state = replace(self.state, latest_observation=observation)

    def get_state(self, scope):
        del scope
        return self.state

    def get_operation(self, scope, operation_id):
        del scope
        if self.operation is not None and self.operation.operation_id == operation_id:
            return self.operation
        return None

    def list_history(self, query):
        del query
        return self.history


class FixedRenderer:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def render(self, template):
        self.calls.append(template)
        return self.result


class FixedConfirmation:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.verify_calls = []
        self.verify_result = ConfirmationAccepted("c" * 64)

    def issue(self, snapshot, now):
        self.calls.append((snapshot, now))
        return self.result

    def verify(self, token, snapshot, now):
        self.verify_calls.append((token, snapshot, now))
        return self.verify_result


class ApplyingRepository(RecordingRepository):
    def __init__(self):
        super().__init__()
        self.accept_calls = []
        self.stage_claims = []
        self.stage_outcomes = []
        self.candidate = None
        self.request_fingerprint = None

    def list_managed_resources(self, scope):
        del scope
        return () if self.candidate is None else (self.candidate,)

    def accept(self, command):
        self.accept_calls.append(command)
        self.request_fingerprint = command.request_fingerprint
        self.operation = OperationView(
            operation_id=command.operation_id,
            kind=command.kind,
            status=OperationStatus.ACCEPTED,
            stage=None,
            result=SafeResultCode.ACCEPTED,
            subject_operation_id=None,
            target_resource_id=None,
            accepted_at=NOW,
            completed_at=None,
            next_allowed_actions=(NextAllowedAction.GET_STATE,),
        )
        self.candidate = ManagedResourceTarget(
            public_id=uuid4(),
            line_rich_menu_id=None,
            lifecycle=ResourceLifecycle.CANDIDATE,
            ownership_marker="lrm:v1:marker",
            origin_operation_id=command.operation_id,
        )
        return OperationAccepted(self.operation, self.candidate.public_id)

    def get_operation_by_id(self, operation_id):
        if self.operation is not None and self.operation.operation_id == operation_id:
            return self.operation
        return None

    def get_request_fingerprint(self, operation_id):
        if self.operation is not None and self.operation.operation_id == operation_id:
            return self.request_fingerprint
        return None

    def get_managed_resource(self, scope, resource_id):
        del scope
        if self.candidate is not None and self.candidate.public_id == resource_id:
            return self.candidate
        return None

    def bind_resource_line_id(self, resource_id, line_rich_menu_id):
        if self.candidate is None or self.candidate.public_id != resource_id:
            return False
        self.candidate = replace(self.candidate, line_rich_menu_id=line_rich_menu_id)
        return True

    def mark_resource_applied(self, resource_id):
        if self.candidate is None or self.candidate.public_id != resource_id:
            return False
        self.candidate = replace(self.candidate, lifecycle=ResourceLifecycle.APPLIED)
        return True

    def discard_candidate(self, resource_id):
        if (
            self.candidate is None
            or self.candidate.public_id != resource_id
            or self.candidate.line_rich_menu_id is not None
        ):
            return False
        self.candidate = replace(self.candidate, lifecycle=ResourceLifecycle.DELETED)
        return True

    def claim_stage(self, operation_id, expected_stage):
        self.stage_claims.append((operation_id, expected_stage))
        if self.operation is None or self.operation.operation_id != operation_id:
            return None
        self.operation = replace(
            self.operation,
            status=OperationStatus.PROCESSING,
            stage=expected_stage,
        )
        return StageClaimed(
            self.operation,
            OperationFenceSnapshot(
                owner_identity_public_id=uuid4(),
                provider_id="provider-1",
                channel_public_id=self.candidate.public_id,
                expected_channel_revision=NOW,
            ),
        )

    def complete_stage(self, outcome):
        self.stage_outcomes.append(outcome)
        self.operation = replace(
            self.operation,
            status=outcome.next_status,
            stage=outcome.next_stage,
            result=outcome.result,
            completed_at=NOW
            if outcome.next_status
            in {OperationStatus.SUCCEEDED, OperationStatus.FAILED}
            else None,
        )
        if outcome.next_status is OperationStatus.CLEANUP_REQUIRED:
            self.candidate = replace(
                self.candidate, lifecycle=ResourceLifecycle.CLEANUP_REQUIRED
            )
        return self.operation


class LifecycleRepository(RecordingRepository):
    def __init__(self, identity_id, channel_id):
        super().__init__()
        self.target = ManagedResourceTarget(
            public_id=uuid4(),
            line_rich_menu_id="managed-line-id",
            lifecycle=ResourceLifecycle.APPLIED,
            ownership_marker="lrm:v1:managed-marker",
            origin_operation_id=uuid4(),
        )
        self.operation = None
        self.request_fingerprint = None
        self.identity_id = identity_id
        self.channel_id = channel_id

    def list_managed_resources(self, scope):
        del scope
        return (self.target,)

    def get_managed_resource(self, scope, resource_id):
        del scope
        return self.target if resource_id == self.target.public_id else None

    def get_operation_by_id(self, operation_id):
        return self.operation if self.operation and self.operation.operation_id == operation_id else None

    def get_request_fingerprint(self, operation_id):
        if self.operation and self.operation.operation_id == operation_id:
            return self.request_fingerprint
        return None

    def accept(self, command):
        self.request_fingerprint = command.request_fingerprint
        self.operation = OperationView(
            operation_id=command.operation_id,
            kind=command.kind,
            status=OperationStatus.ACCEPTED,
            stage=None,
            result=SafeResultCode.ACCEPTED,
            subject_operation_id=None,
            target_resource_id=command.target_resource_id,
            accepted_at=NOW,
            completed_at=None,
            next_allowed_actions=(NextAllowedAction.GET_STATE,),
        )
        return OperationAccepted(self.operation, None)

    def claim_stage(self, operation_id, expected_stage):
        if self.operation is None or self.operation.operation_id != operation_id:
            return None
        self.operation = replace(
            self.operation, status=OperationStatus.PROCESSING, stage=expected_stage
        )
        return StageClaimed(
            self.operation,
            OperationFenceSnapshot(
                owner_identity_public_id=self.identity_id,
                provider_id="provider-1",
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
            ),
        )

    def complete_stage(self, outcome):
        self.operation = replace(
            self.operation,
            status=outcome.next_status,
            stage=outcome.next_stage,
            result=outcome.result,
            completed_at=NOW
            if outcome.next_status in {
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
            }
            else None,
        )
        return self.operation

    def finalize_unlink(self, resource_id):
        if resource_id != self.target.public_id:
            return False
        self.target = replace(self.target, lifecycle=ResourceLifecycle.CLEANUP_REQUIRED)
        return True

    def finalize_release(self, resource_id):
        if resource_id != self.target.public_id:
            return False
        self.target = replace(self.target, lifecycle=ResourceLifecycle.RELEASED)
        return True


class RecoveryRepository(RecordingRepository):
    def __init__(self, identity_id, channel_id, *, cleanup=False):
        super().__init__()
        self.identity_id = identity_id
        self.channel_id = channel_id
        self.cleanup = cleanup
        self.subject = OperationView(
            operation_id=uuid4(),
            kind=OperationKind.APPLY,
            status=OperationStatus.CLEANUP_REQUIRED
            if cleanup
            else OperationStatus.UNKNOWN,
            stage=OperationStage.CLEANING if cleanup else OperationStage.CREATING,
            result=SafeResultCode.CLEANUP_REQUIRED
            if cleanup
            else SafeResultCode.RESPONSE_UNKNOWN,
            subject_operation_id=None,
            target_resource_id=None,
            accepted_at=NOW,
            completed_at=None,
            next_allowed_actions=(
                NextAllowedAction.CLEANUP
                if cleanup
                else NextAllowedAction.RECHECK,
            ),
        )
        self.candidate = ManagedResourceTarget(
            public_id=uuid4(),
            line_rich_menu_id="cleanup-line-id" if cleanup else None,
            lifecycle=ResourceLifecycle.CLEANUP_REQUIRED
            if cleanup
            else ResourceLifecycle.CANDIDATE,
            ownership_marker="lrm:v1:recovery-marker",
            origin_operation_id=self.subject.operation_id,
        )
        self.recovery = None
        self.request_fingerprint = None

    def list_managed_resources(self, scope):
        del scope
        return (self.candidate,)

    def get_candidate_for_operation(self, scope, operation_id):
        del scope
        return self.candidate if self.candidate.origin_operation_id == operation_id else None

    def get_managed_resource(self, scope, resource_id):
        del scope
        return self.candidate if resource_id == self.candidate.public_id else None

    def bind_resource_line_id(self, resource_id, line_rich_menu_id):
        if resource_id != self.candidate.public_id:
            return False
        self.candidate = replace(
            self.candidate, line_rich_menu_id=line_rich_menu_id
        )
        return True

    def get_operation(self, scope, operation_id):
        del scope
        if self.subject.operation_id == operation_id:
            return self.subject
        if self.recovery is not None and self.recovery.operation_id == operation_id:
            return self.recovery
        return None

    def get_operation_by_id(self, operation_id):
        return self.get_operation(None, operation_id)

    def get_request_fingerprint(self, operation_id):
        if self.recovery is not None and self.recovery.operation_id == operation_id:
            return self.request_fingerprint
        return None

    def accept_recovery(self, command):
        self.request_fingerprint = command.request_fingerprint
        self.recovery = OperationView(
            operation_id=command.operation_id,
            kind=command.kind,
            status=OperationStatus.RECOVERY_ACTIVE,
            stage=OperationStage.CLEANING
            if command.kind is OperationKind.CLEANUP
            else OperationStage.VERIFYING,
            result=SafeResultCode.ACCEPTED,
            subject_operation_id=command.subject_operation_id,
            target_resource_id=command.target_resource_id,
            accepted_at=NOW,
            completed_at=None,
            next_allowed_actions=(NextAllowedAction.GET_STATE,),
        )
        return RecoveryAccepted(self.recovery)

    def complete_recovery(self, operation_id, next_status, result):
        self.recovery = replace(
            self.recovery,
            status=next_status,
            result=result,
            completed_at=NOW,
        )
        return self.recovery

    def handoff_recovery(self, outcome):
        self.recovery = replace(
            self.recovery,
            status=OperationStatus.SUCCEEDED,
            result=SafeResultCode.SUCCEEDED,
            completed_at=NOW,
        )
        self.subject = replace(
            self.subject,
            status=outcome.subject_next_status,
            stage=outcome.subject_next_stage,
            result=outcome.subject_result,
        )
        return RecoveryHandoffResult(self.recovery, self.subject)

    def complete_cleanup_recovery(self, operation_id, resource_id):
        if resource_id != self.candidate.public_id:
            return None
        self.candidate = replace(self.candidate, lifecycle=ResourceLifecycle.DELETED)
        self.recovery = replace(
            self.recovery,
            status=OperationStatus.SUCCEEDED,
            result=SafeResultCode.SUCCEEDED,
            completed_at=NOW,
        )
        return self.recovery

    def finalize_deleted(self, resource_id):
        if resource_id != self.candidate.public_id:
            return False
        self.candidate = replace(self.candidate, lifecycle=ResourceLifecycle.DELETED)
        return True


def _input():
    return TemplateInput(
        reference=TemplateReference("jp-link-one", 1),
        fields={
            "area1": {
                "displayName": "公式サイト",
                "uri": "https://example.com/guide",
            }
        },
    )


def _snapshot(channel_id, identity_id, *, active=True):
    return RichMenuChannelSnapshot(
        owner_identity_public_id=identity_id,
        provider_id="provider-1",
        channel_public_id=channel_id,
        channel_label="学習用チャネル",
        is_active=active,
        channel_revision=NOW,
        access_token=AccessToken("access-token-canary"),
    )


def _reconciliation(kind):
    return Reconciliation(
        observation=DefaultObservation(
            kind=kind,
            observed_at=NOW,
            fingerprint="a" * 64,
            managed_resource_id=None,
        ),
        next_allowed_actions=(),
    )


def _empty_state(channel_id):
    return ChannelStateView(
        channel_public_id=channel_id,
        current_resource=None,
        blocking_operation=None,
        active_operation=None,
        cleanup_resources=(),
        latest_observation=None,
        history_summary=HistorySummary(
            total_count=0,
            latest_operation_id=None,
            latest_status=None,
        ),
        next_allowed_actions=(),
    )


def _operation(owner_id, channel_id, *, status=OperationStatus.SUCCEEDED):
    return OperationView(
        operation_id=uuid4(),
        kind=OperationKind.APPLY,
        status=status,
        stage=OperationStage.VERIFYING,
        result=SafeResultCode.SUCCEEDED,
        subject_operation_id=None,
        target_resource_id=None,
        accepted_at=NOW,
        completed_at=NOW,
        next_allowed_actions=(NextAllowedAction.GET_STATE,),
    )


class RichMenuPreviewServiceTests(TransactionTestCase):
    def setUp(self):
        self.identity_id = uuid4()
        self.channel_id = uuid4()
        self.owner = OwnerOperationContext(uuid4(), self.identity_id)
        self.owner_fence = RecordingOwnerFence(identity_id=self.identity_id)
        self.channel_port = RecordingChannelPort(
            _snapshot(self.channel_id, self.identity_id)
        )
        self.gateway = RecordingGateway(RichMenuDefaultExternal())
        self.reconciler = DefaultRichMenuReconciler(self.gateway)
        self.repository = RecordingRepository()
        self.renderer = DefaultDeterministicRenderer()
        self.confirmation = FixedConfirmation(
            IssuedConfirmation(
                token="confirmation-token",
                expires_at=NOW,
                usage_digest="b" * 64,
            )
        )
        self.service = DefaultRichMenuService(
            owner_fence=self.owner_fence,
            channel_port=self.channel_port,
            repository=self.repository,
            gateway=self.gateway,
            reconciler=self.reconciler,
            renderer=self.renderer,
            confirmation=self.confirmation,
            clock=lambda: NOW,
        )

    # テストケース: 有効な入力から外部defaultを含むpreviewを生成する。
    # 期待値: LINE object validateとdefault観測後だけtokenを発行し、previewに画像と警告を含める。
    def test_preview_returns_image_confirmation_and_external_default_warning(self):
        result = self.service.preview(
            self.owner,
            PreviewRequest(
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
                template=_input(),
            ),
        )

        self.assertIsInstance(result, PreviewSucceeded)
        self.assertEqual(result.preview.channel_label, "学習用チャネル")
        self.assertEqual(result.preview.template.fields[0].display_name, "公式サイト")
        self.assertEqual(result.preview.observation.kind, ObservationKind.EXTERNAL_DEFAULT)
        self.assertIn("external_default_replaced", {warning.value for warning in result.preview.warnings})
        self.assertTrue(result.token)
        self.assertTrue(result.image_base64)
        self.assertEqual([call[0] for call in self.gateway.calls], ["validate", "get_default"])
        self.assertEqual(len(self.repository.observations), 1)

    # テストケース: default観測がunknownになるpreviewを要求する。
    # 期待値: tokenを発行せず、unknownを安全な失敗として返す。
    def test_preview_does_not_issue_confirmation_when_default_is_unknown(self):
        self.gateway.default = RichMenuDefaultUnknown("timeout_unknown")
        self.reconciler.reconciliation = _reconciliation(ObservationKind.UNKNOWN)

        result = self.service.preview(
            self.owner,
            PreviewRequest(
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
                template=_input(),
            ),
        )

        self.assertIsInstance(result, ServiceFailed)
        self.assertEqual(result.code, SafeResultCode.OBSERVATION_UNKNOWN)
        self.assertEqual(result.token, None)
        self.assertEqual(self.confirmation.calls, [])

    # テストケース: inactive channelでpreviewを要求する。
    # 期待値: exact snapshotのinactive結果を返し、LINE gatewayへ到達しない。
    def test_preview_does_not_call_line_for_inactive_channel(self):
        self.channel_port.snapshot = _snapshot(
            self.channel_id, self.identity_id, active=False
        )

        result = self.service.preview(
            self.owner,
            PreviewRequest(
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
                template=_input(),
            ),
        )

        self.assertIsInstance(result, ServiceFailed)
        self.assertEqual(result.code, SafeResultCode.CHANNEL_INACTIVE)
        self.assertEqual(self.gateway.calls, [])

    # テストケース: 必須項目が欠けたstrict template inputでpreviewを要求する。
    # 期待値: field-level invalid inputを返し、render・LINE・confirmationを実行しない。
    def test_preview_rejects_strict_input_before_line_validation(self):
        invalid = TemplateInput(
            reference=TemplateReference("jp-link-one", 1),
            fields={"area1": {"displayName": "公式サイト"}},
        )

        result = self.service.preview(
            self.owner,
            PreviewRequest(
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
                template=invalid,
            ),
        )

        self.assertIsInstance(result, ServiceFailed)
        self.assertEqual(result.code, SafeResultCode.INVALID_INPUT)
        self.assertEqual(result.errors, (InputFieldError("area1.uri", "required"),))
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.confirmation.calls, [])

    # テストケース: rendererが画像制約違反を返すpreviewを要求する。
    # 期待値: image_invalidを返し、LINE object validateを開始しない。
    def test_preview_rejects_render_failure_before_line_validation(self):
        self.service._renderer = FixedRenderer(
            RenderRejected(code=SafeResultCode.IMAGE_INVALID)
        )

        result = self.service.preview(
            self.owner,
            PreviewRequest(
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
                template=_input(),
            ),
        )

        self.assertIsInstance(result, ServiceFailed)
        self.assertEqual(result.code, SafeResultCode.IMAGE_INVALID)
        self.assertEqual(self.gateway.calls, [])

    # テストケース: active channelの状態照会で最新defaultを観測する。
    # 期待値: 保存projectionへ観測を合成し、ownerが次の判断に使えるstateを返す。
    def test_get_state_refreshes_active_projection_from_latest_observation(self):
        self.repository.state = _empty_state(self.channel_id)

        result = self.service.get_state(self.owner, self.channel_id)

        self.assertIsInstance(result, StateSucceeded)
        self.assertEqual(
            result.state.latest_observation.kind,
            ObservationKind.EXTERNAL_DEFAULT,
        )
        self.assertEqual(len(self.repository.observations), 1)
        self.assertEqual([call[0] for call in self.gateway.calls], ["get_default"])

    # テストケース: inactive channelの状態照会を行う。
    # 期待値: 保存projectionだけを返し、LINE観測を開始しない。
    def test_get_state_does_not_call_line_for_inactive_channel(self):
        self.channel_port.snapshot = _snapshot(
            self.channel_id, self.identity_id, active=False
        )
        self.repository.state = _empty_state(self.channel_id)

        result = self.service.get_state(self.owner, self.channel_id)

        self.assertIsInstance(result, StateSucceeded)
        self.assertIs(result.state, self.repository.state)
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.repository.observations, [])

    # テストケース: operation単体とowner履歴を照会する。
    # 期待値: repositoryのscope結果をoperation/history wrapperへ返し、LINEを呼ばない。
    def test_operation_and_history_reads_are_owner_scoped_and_local(self):
        operation = _operation(self.identity_id, self.channel_id)
        self.repository.operation = operation
        self.repository.history = HistoryPage(entries=(), next_cursor=None, has_more=False)
        scope = OwnerChannelScope(
            owner_identity_public_id=self.identity_id,
            provider_id="provider-1",
            channel_public_id=self.channel_id,
        )

        operation_result = self.service.get_operation(self.owner, operation.operation_id)
        history_result = self.service.list_history(
            self.owner,
            HistoryQuery(scope=scope, limit=10),
        )

        self.assertIsInstance(operation_result, OperationSucceeded)
        self.assertIs(operation_result.operation, operation)
        self.assertIsInstance(history_result, HistorySucceeded)
        self.assertIs(history_result.history, self.repository.history)
        self.assertEqual(self.gateway.calls, [])


class RichMenuApplyServiceTests(TransactionTestCase):
    def setUp(self):
        self.identity_id = uuid4()
        self.channel_id = uuid4()
        self.owner = OwnerOperationContext(uuid4(), self.identity_id)
        self.owner_fence = RecordingOwnerFence(identity_id=self.identity_id)
        self.channel_port = RecordingChannelPort(
            _snapshot(self.channel_id, self.identity_id)
        )
        self.gateway = RecordingGateway(RichMenuDefaultExternal())
        self.repository = ApplyingRepository()
        self.confirmation = FixedConfirmation(
            IssuedConfirmation(
                token="confirmation-token",
                expires_at=NOW,
                usage_digest="b" * 64,
            )
        )
        self.service = DefaultRichMenuService(
            owner_fence=self.owner_fence,
            channel_port=self.channel_port,
            repository=self.repository,
            gateway=self.gateway,
            reconciler=DefaultRichMenuReconciler(self.gateway),
            renderer=DefaultDeterministicRenderer(),
            confirmation=self.confirmation,
            readiness=DefaultMutationReadiness(
                mode="enabled", integration_complete=True
            ),
            clock=lambda: NOW,
        )

    def _command(self, operation_id=None, *, template=None):
        return OperationCommand(
            operation_id=operation_id or uuid4(),
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
            kind=OperationKind.APPLY,
            subject_operation_id=None,
            target_resource_id=None,
            confirmation_token="confirmation-token",
            template=template or _input(),
        )

    # テストケース: 確認済み設定を新規operationとしてapplyする。
    # 期待値: 候補を一件だけ予約し、marker付きcreateとuploadを順序どおり実行して次stageへ進む。
    def test_apply_reserves_one_candidate_and_runs_create_then_upload(self):
        operation_id = uuid4()
        result = self.service.start_operation(
            self.owner,
            self._command(operation_id),
        )

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.SUCCEEDED)
        self.assertEqual(result.operation.stage, OperationStage.VERIFYING)
        self.assertEqual(len(self.repository.accept_calls), 1)
        self.assertEqual(
            [call[0] for call in self.gateway.calls],
            [
                "get_default",
                "create",
                "upload",
                "get_default",
                "set_default",
                "get_default",
            ],
        )
        self.assertEqual(
            [stage for _, stage in self.repository.stage_claims],
            [
                OperationStage.CREATING,
                OperationStage.UPLOADING,
                OperationStage.SETTING_DEFAULT,
                OperationStage.VERIFYING,
            ],
        )
        self.assertEqual(self.repository.candidate.line_rich_menu_id, "rich-menu-created")
        self.assertEqual(
            self.gateway.calls[1][2].name,
            self.repository.candidate.ownership_marker,
        )

    # テストケース: createが明示拒否されるapplyを要求する。
    # 期待値: upload/setは開始せず、候補なしのfailedへ収束する。
    def test_create_rejection_does_not_start_upload(self):
        self.gateway.create_result = GatewayRejected("line_rejected")

        result = self.service.start_operation(self.owner, self._command())

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.FAILED)
        self.assertEqual(result.operation.result, SafeResultCode.LINE_REJECTED)
        self.assertEqual(
            [call[0] for call in self.gateway.calls], ["get_default", "create"]
        )
        self.assertEqual(self.repository.candidate.lifecycle, ResourceLifecycle.DELETED)

    # テストケース: create後のuploadが明示拒否されるapplyを要求する。
    # 期待値: candidateを自動削除せずcleanup_requiredへ保存し、set defaultを開始しない。
    def test_upload_rejection_preserves_candidate_for_cleanup(self):
        self.gateway.upload_result = GatewayRejected("line_rejected")

        result = self.service.start_operation(self.owner, self._command())

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.CLEANUP_REQUIRED)
        self.assertEqual(
            self.repository.candidate.lifecycle, ResourceLifecycle.CLEANUP_REQUIRED
        )
        self.assertNotIn("set_default", [call[0] for call in self.gateway.calls])


class RichMenuLifecycleServiceTests(TransactionTestCase):
    def setUp(self):
        self.identity_id = uuid4()
        self.channel_id = uuid4()
        self.owner = OwnerOperationContext(uuid4(), self.identity_id)
        self.owner_fence = RecordingOwnerFence(identity_id=self.identity_id)
        self.channel_port = RecordingChannelPort(
            _snapshot(self.channel_id, self.identity_id)
        )
        self.gateway = RecordingGateway(RichMenuDefaultExternal())
        self.repository = LifecycleRepository(self.identity_id, self.channel_id)
        self.service = DefaultRichMenuService(
            owner_fence=self.owner_fence,
            channel_port=self.channel_port,
            repository=self.repository,
            gateway=self.gateway,
            reconciler=DefaultRichMenuReconciler(self.gateway),
            readiness=DefaultMutationReadiness(
                mode="enabled", integration_complete=True
            ),
            clock=lambda: NOW,
        )

    def _command(self, kind):
        return OperationCommand(
            operation_id=uuid4(),
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
            kind=kind,
            subject_operation_id=None,
            target_resource_id=self.repository.target.public_id,
        )

    # テストケース: 外部default中の管理対象をunlinkする。
    # 期待値: 外部defaultへclearせず、対象だけを非defaultのcleanup対象へ収束する。
    def test_unlink_preserves_external_default(self):
        result = self.service.start_operation(
            self.owner, self._command(OperationKind.UNLINK)
        )

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.SUCCEEDED)
        self.assertEqual(self.repository.target.lifecycle, ResourceLifecycle.CLEANUP_REQUIRED)
        self.assertNotIn("clear_default", [call[0] for call in self.gateway.calls])

    # テストケース: 管理対象が現在defaultのunlinkを要求する。
    # 期待値: 一致時だけclearし、非default確認後にclear_to_disableを返す。
    def test_unlink_clears_only_matching_managed_default(self):
        self.gateway.default = RichMenuDefaultPresent("managed-line-id")

        result = self.service.start_operation(
            self.owner, self._command(OperationKind.UNLINK)
        )

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.SUCCEEDED)
        self.assertIn(
            NextAllowedAction.CLEAR_TO_DISABLE,
            result.operation.next_allowed_actions,
        )
        self.assertIn("clear_default", [call[0] for call in self.gateway.calls])

    # テストケース: 管理終了を要求する。
    # 期待値: LINE callを一件も行わず、対象resourceだけをreleasedへ移す。
    def test_release_does_not_call_line_and_marks_resource_released(self):
        result = self.service.start_operation(
            self.owner, self._command(OperationKind.RELEASE)
        )

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.SUCCEEDED)
        self.assertEqual(self.repository.target.lifecycle, ResourceLifecycle.RELEASED)
        self.assertEqual(self.gateway.calls, [])


class RichMenuApplyContinuationTests(TransactionTestCase):
    def setUp(self):
        self.identity_id = uuid4()
        self.channel_id = uuid4()
        self.owner = OwnerOperationContext(uuid4(), self.identity_id)
        self.owner_fence = RecordingOwnerFence(identity_id=self.identity_id)
        self.channel_port = RecordingChannelPort(
            _snapshot(self.channel_id, self.identity_id)
        )
        self.gateway = RecordingGateway(RichMenuDefaultExternal())
        self.repository = ApplyingRepository()
        self.confirmation = FixedConfirmation(
            IssuedConfirmation(
                token="confirmation-token",
                expires_at=NOW,
                usage_digest="b" * 64,
            )
        )
        self.service = DefaultRichMenuService(
            owner_fence=self.owner_fence,
            channel_port=self.channel_port,
            repository=self.repository,
            gateway=self.gateway,
            reconciler=DefaultRichMenuReconciler(self.gateway),
            renderer=DefaultDeterministicRenderer(),
            confirmation=self.confirmation,
            readiness=DefaultMutationReadiness(
                mode="enabled", integration_complete=True
            ),
            clock=lambda: NOW,
        )

    def _command(self, operation_id=None, *, template=None):
        return OperationCommand(
            operation_id=operation_id or uuid4(),
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
            kind=OperationKind.APPLY,
            subject_operation_id=None,
            target_resource_id=None,
            confirmation_token="confirmation-token",
            template=template or _input(),
        )

    # テストケース: createの結果がunknownになるapplyを要求する。
    # 期待値: 同じrequest内でuploadを再実行せず、creatingのunknownを保存する。
    def test_create_unknown_stops_without_upload_retry(self):
        self.gateway.create_result = GatewayUnknown("timeout_unknown")

        result = self.service.start_operation(self.owner, self._command())

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.UNKNOWN)
        self.assertEqual(result.operation.stage, OperationStage.CREATING)
        self.assertEqual(
            [call[0] for call in self.gateway.calls], ["get_default", "create"]
        )

    # テストケース: 同じoperationを期限切れtokenで再送する。
    # 期待値: 保存済み結果を返し、confirmation検証とLINE外部作用を再実行しない。
    def test_same_operation_replay_returns_saved_result_before_confirmation(self):
        operation_id = uuid4()
        first = self.service.start_operation(self.owner, self._command(operation_id))
        self.assertIsInstance(first, OperationSucceeded)
        self.gateway.calls.clear()
        self.confirmation.verify_result = ConfirmationRejected("preview_expired")

        replay = self.service.start_operation(self.owner, self._command(operation_id))

        self.assertIsInstance(replay, OperationSucceeded)
        self.assertEqual(replay.operation.operation_id, operation_id)
        self.assertEqual(len(self.repository.accept_calls), 1)
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(len(self.confirmation.verify_calls), 1)

    # テストケース: create/upload後にpreview時defaultとの差分が発生する。
    # 期待値: set defaultを開始せずcandidateをcleanup_requiredへ残す。
    def test_default_drift_before_set_preserves_old_state_and_candidate(self):
        self.gateway.default_sequence = [
            RichMenuDefaultExternal(),
            RichMenuDefaultNone(),
        ]

        result = self.service.start_operation(self.owner, self._command())

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.CLEANUP_REQUIRED)
        self.assertEqual(
            self.repository.candidate.lifecycle, ResourceLifecycle.CLEANUP_REQUIRED
        )
        self.assertNotIn("set_default", [call[0] for call in self.gateway.calls])

    # テストケース: uploadの結果がunknownになるapplyを要求する。
    # 期待値: cleanupへ確定せずupload stageのunknownを保存し、setを開始しない。
    def test_upload_unknown_stops_without_cleanup_or_retry(self):
        self.gateway.upload_result = GatewayUnknown("timeout_unknown")

        result = self.service.start_operation(self.owner, self._command())

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.UNKNOWN)
        self.assertEqual(result.operation.stage, OperationStage.UPLOADING)
        self.assertEqual(self.repository.candidate.lifecycle, ResourceLifecycle.CANDIDATE)
        self.assertNotIn("set_default", [call[0] for call in self.gateway.calls])


class RichMenuRecoveryServiceTests(TransactionTestCase):
    def _build_service(self, *, cleanup=False):
        identity_id = uuid4()
        channel_id = uuid4()
        owner = OwnerOperationContext(uuid4(), identity_id)
        owner_fence = RecordingOwnerFence(identity_id=identity_id)
        channel_port = RecordingChannelPort(_snapshot(channel_id, identity_id))
        gateway = RecordingGateway(RichMenuDefaultExternal())
        repository = RecoveryRepository(identity_id, channel_id, cleanup=cleanup)
        reconciler = RecordingReconciler(_reconciliation(ObservationKind.EXTERNAL_DEFAULT))
        service = DefaultRichMenuService(
            owner_fence=owner_fence,
            channel_port=channel_port,
            repository=repository,
            gateway=gateway,
            reconciler=reconciler,
            readiness=DefaultMutationReadiness(
                mode="enabled", integration_complete=True
            ),
            clock=lambda: NOW,
        )
        return owner, service, repository, gateway, reconciler, channel_id

    # テストケース: create unknownを明示recheckする。
    # 期待値: 作成・uploadを再実行せずcandidateをbindして次stageへhandoffする。
    def test_recheck_confirms_create_without_repeating_mutation(self):
        owner, service, repository, gateway, reconciler, channel_id = self._build_service()
        reconciler.recheck_result = RecheckConfirmed(
            OperationStage.CREATING,
            line_rich_menu_id="recovered-line-id",
            resource_id=repository.candidate.public_id,
            next_stage=OperationStage.UPLOADING,
        )
        command = OperationCommand(
            operation_id=uuid4(),
            channel_public_id=channel_id,
            expected_channel_revision=NOW,
            kind=OperationKind.RECHECK,
            subject_operation_id=repository.subject.operation_id,
            target_resource_id=None,
        )

        result = service.start_operation(owner, command)

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.SUCCEEDED)
        self.assertEqual(repository.candidate.line_rich_menu_id, "recovered-line-id")
        self.assertEqual(gateway.calls, [])

    # テストケース: recheck観測自体がunknownになる。
    # 期待値: recoveryをunknownへ保存し、元の外部作用を再実行しない。
    def test_recheck_unknown_does_not_retry_external_mutation(self):
        owner, service, repository, gateway, reconciler, channel_id = self._build_service()
        reconciler.recheck_result = RecheckUnknown(
            OperationStage.CREATING, "observation_unknown"
        )
        command = OperationCommand(
            operation_id=uuid4(),
            channel_public_id=channel_id,
            expected_channel_revision=NOW,
            kind=OperationKind.RECHECK,
            subject_operation_id=repository.subject.operation_id,
            target_resource_id=None,
        )

        result = service.start_operation(owner, command)

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.UNKNOWN)
        self.assertEqual(gateway.calls, [])

    # テストケース: cleanup対象のownershipとdefault非一致を確認して削除する。
    # 期待値: 対象一件だけをdeleteし、deletedとcleanup recovery成功へ収束する。
    def test_cleanup_deletes_only_confirmed_non_default_target(self):
        owner, service, repository, gateway, reconciler, channel_id = self._build_service(
            cleanup=True
        )
        reconciler.recheck_result = RecheckConfirmed(
            OperationStage.CLEANING,
            line_rich_menu_id=repository.candidate.line_rich_menu_id,
            resource_id=repository.candidate.public_id,
        )
        command = OperationCommand(
            operation_id=uuid4(),
            channel_public_id=channel_id,
            expected_channel_revision=NOW,
            kind=OperationKind.CLEANUP,
            subject_operation_id=repository.subject.operation_id,
            target_resource_id=repository.candidate.public_id,
        )

        result = service.start_operation(owner, command)

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.SUCCEEDED)
        self.assertEqual(repository.candidate.lifecycle, ResourceLifecycle.DELETED)
        self.assertEqual([call[0] for call in gateway.calls], ["delete"])

    # テストケース: cleanup対象が現在defaultとして観測される。
    # 期待値: deleteせずunknown blockerを維持する。
    def test_cleanup_does_not_delete_current_default(self):
        owner, service, repository, gateway, reconciler, channel_id = self._build_service(
            cleanup=True
        )
        reconciler.recheck_result = RecheckConfirmed(
            OperationStage.CLEANING,
            line_rich_menu_id=repository.candidate.line_rich_menu_id,
            resource_id=repository.candidate.public_id,
        )
        reconciler.reconciliation = Reconciliation(
            observation=DefaultObservation(
                kind=ObservationKind.MANAGED_DEFAULT,
                observed_at=NOW,
                fingerprint="d" * 64,
                managed_resource_id=repository.candidate.public_id,
            ),
            next_allowed_actions=(NextAllowedAction.RECHECK,),
        )
        command = OperationCommand(
            operation_id=uuid4(),
            channel_public_id=channel_id,
            expected_channel_revision=NOW,
            kind=OperationKind.CLEANUP,
            subject_operation_id=repository.subject.operation_id,
            target_resource_id=repository.candidate.public_id,
        )

        result = service.start_operation(owner, command)

        self.assertIsInstance(result, OperationSucceeded)
        self.assertEqual(result.operation.status, OperationStatus.UNKNOWN)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(repository.candidate.lifecycle, ResourceLifecycle.CLEANUP_REQUIRED)
