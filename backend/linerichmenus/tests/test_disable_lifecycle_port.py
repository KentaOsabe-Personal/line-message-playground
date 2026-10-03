from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from django.test import TestCase, TransactionTestCase

from lineaccounts.admin_authorization import OwnerOperationContext
from linechannels.reference_fence import ReferenceFenceResult
from linerichmenus.headless import (
    DefaultRichMenuLifecyclePort,
    DisableAssessment,
    DisableRecoveryLookup,
    HeadlessStateCommand,
    HeadlessUnlinkCommand,
    ReassessDisableState,
    ReconcileDisableSubject,
)
from linerichmenus.models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation
from linerichmenus.repository import (
    DisableUnlinkRejected,
    DjangoRichMenuRepository,
    ReservedDisableUnlink,
    ReserveDisableUnlink,
    disable_assessment_proof,
)
from linerichmenus.services import OperationSucceeded, StateSucceeded
from linerichmenus.types import (
    ChannelStateView,
    DefaultObservation,
    HistorySummary,
    ManagedResourceView,
    NextAllowedAction,
    ObservationKind,
    OperationKind,
    OperationStage,
    OperationStatus,
    OperationView,
    ResourceLifecycle,
    SafeResultCode,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class _ChannelOwnedRecoveryStore:
    def __init__(self):
        self.records = {}

    def lookup(self, command):
        saved = self.records.get(command.recovery_operation_id)
        if saved is None:
            return DisableRecoveryLookup()
        saved_command, assessment = saved
        return (
            DisableRecoveryLookup(assessment=assessment)
            if saved_command == command
            else DisableRecoveryLookup(conflict=True)
        )

    def save(self, command, assessment):
        existing = self.lookup(command)
        if existing.assessment is not None or existing.conflict:
            return existing
        self.records[command.recovery_operation_id] = (command, assessment)
        return DisableRecoveryLookup(assessment=assessment)


class _FailingRecoveryStore:
    def lookup(self, command):
        del command
        raise RuntimeError("database-canary")

    def save(self, command, assessment):
        del command, assessment
        raise RuntimeError("database-canary")


class DisableLifecycleAssessmentTests(TestCase):
    def setUp(self):
        self.owner = OwnerOperationContext(uuid4(), uuid4())
        self.channel_id = uuid4()
        self.resource_id = uuid4()
        self.command = HeadlessStateCommand(
            owner=self.owner,
            provider_id="0012345678",
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
        )

    # テストケース: 現在既定と一致する管理対象をdisable向けに評価する。
    # 期待値: LINE資源IDを返さず、opaque proofと管理対象UUIDだけのunlink_requiredになる。
    def test_managed_default_becomes_opaque_unlink_required_assessment(self):
        service = Mock()
        service.get_state.return_value = StateSucceeded(self._state())

        result = DefaultRichMenuLifecyclePort(service).assess_disable(self.command)

        self.assertEqual(result.status, "unlink_required")
        self.assertEqual(result.target_resource_id, self.resource_id)
        self.assertEqual(len(result.assessment_proof), 64)
        self.assertNotIn("line-rich-menu", repr(result))

    # テストケース: defaultなし、外部既定、結果不明、後片付け待ちを評価する。
    # 期待値: 各状態が自動外部作用を起こさないclosed resultへ分類される。
    def test_assessment_maps_all_non_mutating_closed_variants(self):
        cases = (
            (
                self._state(current=False, observation=ObservationKind.DEFAULT_NONE),
                "clear_to_disable",
            ),
            (
                self._state(current=False, observation=ObservationKind.EXTERNAL_DEFAULT),
                "external_default_blocked",
            ),
            (self._state(current=False, observation=ObservationKind.UNKNOWN), "unavailable"),
            (self._state(cleanup=True), "cleanup_required"),
        )

        for state, expected in cases:
            with self.subTest(expected=expected):
                service = Mock()
                service.get_state.return_value = StateSucceeded(state)
                result = DefaultRichMenuLifecyclePort(service).assess_disable(self.command)
                self.assertEqual(result.status, expected)

    # テストケース: closed assessmentへ別variant専用fieldの組合せを渡す。
    # 期待値: clearへのsubject、subjectなしrecheck、targetなしcleanupを構築時に拒否する。
    def test_assessment_rejects_cross_variant_field_combinations(self):
        invalid = (
            lambda: DisableAssessment("clear_to_disable", subject_operation_id=uuid4()),
            lambda: DisableAssessment("recheck_required", reason="recheck_required"),
            lambda: DisableAssessment("cleanup_required", reason="cleanup_required"),
        )

        for factory in invalid:
            with self.subTest(factory=factory):
                with self.assertRaises(ValueError):
                    factory()

    # テストケース: unlink commandをcaller targetなしで構築する。
    # 期待値: deactivation IDとassessment proofだけを受け、target指定の余地を公開しない。
    def test_unlink_command_exposes_no_caller_selected_target(self):
        command = HeadlessUnlinkCommand(
            owner=self.owner,
            provider_id="0012345678",
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
            deactivation_operation_id=uuid4(),
            assessment_proof="a" * 64,
        )

        self.assertFalse(hasattr(command, "target_resource_id"))

    # テストケース: 保存subjectありの再確認とsubjectなしの再評価を実行する。
    # 期待値: 前者だけが元operationのrecheckを開始し、後者は状態評価以外の外部作用を作らない。
    def test_recovery_separates_subject_recheck_from_non_mutating_reassessment(self):
        service = Mock()
        service.get_state.return_value = StateSucceeded(
            self._state(current=False, observation=ObservationKind.DEFAULT_NONE)
        )
        subject_id = uuid4()
        recovery_id = uuid4()
        operation = OperationView(
            recovery_id,
            OperationKind.RECHECK,
            OperationStatus.SUCCEEDED,
            OperationStage.VERIFYING,
            SafeResultCode.SUCCEEDED,
            subject_id,
            None,
            NOW,
            NOW,
            (),
        )
        service.start_operation.return_value = OperationSucceeded(operation)
        port = DefaultRichMenuLifecyclePort(service, recovery_store=_ChannelOwnedRecoveryStore())

        reconciled = port.recover_disable(
            ReconcileDisableSubject(
                owner=self.owner,
                provider_id="0012345678",
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
                deactivation_operation_id=uuid4(),
                recovery_operation_id=recovery_id,
                subject_operation_id=subject_id,
            )
        )
        reassessed = port.recover_disable(
            ReassessDisableState(
                owner=self.owner,
                provider_id="0012345678",
                channel_public_id=self.channel_id,
                expected_channel_revision=NOW,
                deactivation_operation_id=uuid4(),
                recovery_operation_id=uuid4(),
                reason="external_default",
            )
        )

        sent = service.start_operation.call_args.args[1]
        self.assertEqual(sent.kind, OperationKind.RECHECK)
        self.assertEqual(sent.subject_operation_id, subject_id)
        self.assertEqual(reconciled.assessment.status, "clear_to_disable")
        self.assertEqual(reassessed.assessment.status, "clear_to_disable")
        self.assertEqual(service.start_operation.call_count, 1)

    # テストケース: subjectなしreassessmentを同じrecovery IDで再送する。
    # 期待値: 最初のassessmentを永続replayし、変化後の状態を再評価しない。
    def test_reassessment_replays_persisted_result_by_recovery_id(self):
        service = Mock()
        service.get_state.return_value = StateSucceeded(
            self._state(current=False, observation=ObservationKind.DEFAULT_NONE)
        )
        command = ReassessDisableState(
            owner=self.owner,
            provider_id="0012345678",
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
            deactivation_operation_id=uuid4(),
            recovery_operation_id=uuid4(),
            reason="external_default",
        )
        port = DefaultRichMenuLifecyclePort(service, recovery_store=_ChannelOwnedRecoveryStore())

        first = port.recover_disable(command)
        service.get_state.return_value = StateSucceeded(
            self._state(current=False, observation=ObservationKind.EXTERNAL_DEFAULT)
        )
        replay = port.recover_disable(command)

        self.assertEqual(first.assessment.status, "clear_to_disable")
        self.assertEqual(replay.assessment, first.assessment)
        self.assertEqual(service.get_state.call_count, 1)

    # テストケース: ChannelDeactivationRepository adapterの保存境界が失敗する。
    # 期待値: 例外や内部値を流出させずclosed unavailableへ縮約する。
    def test_recovery_store_failure_is_closed_as_unavailable(self):
        service = Mock()
        command = ReassessDisableState(
            owner=self.owner,
            provider_id="0012345678",
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
            deactivation_operation_id=uuid4(),
            recovery_operation_id=uuid4(),
            reason="external_default",
        )

        result = DefaultRichMenuLifecyclePort(
            service, recovery_store=_FailingRecoveryStore()
        ).recover_disable(command)

        self.assertEqual(result.assessment.status, "unavailable")
        self.assertEqual(result.assessment.reason, "storage_unavailable")
        self.assertNotIn("database-canary", repr(result))
        service.get_state.assert_not_called()

    def _state(self, *, current=True, observation=ObservationKind.MANAGED_DEFAULT, cleanup=False):
        resource = ManagedResourceView(
            self.resource_id, uuid4(), ResourceLifecycle.APPLIED, "b" * 64
        )
        observed_resource = (
            self.resource_id
            if observation
            in {
                ObservationKind.MANAGED_DEFAULT,
                ObservationKind.OTHER_MANAGED_DEFAULT,
            }
            else None
        )
        return ChannelStateView(
            channel_public_id=self.channel_id,
            current_resource=resource if current else None,
            blocking_operation=None,
            active_operation=None,
            cleanup_resources=(resource,) if cleanup else (),
            latest_observation=DefaultObservation(observation, NOW, "c" * 64, observed_resource),
            history_summary=HistorySummary(0, None, None),
            next_allowed_actions=(NextAllowedAction.UNLINK,),
        )


class _LockedFence:
    def lock_existing(self, channel_public_id):
        del channel_public_id
        return ReferenceFenceResult("locked")


class _MatchedOperationFence:
    def lock_exact(self, snapshot):
        del snapshot
        from linerichmenus.repository import OperationFenceResult

        return OperationFenceResult("matched")


class DisableUnlinkReservationTests(TransactionTestCase):
    def setUp(self):
        self.repository = DjangoRichMenuRepository(
            reference_fence=_LockedFence(),
            operation_fence=_MatchedOperationFence(),
            clock=lambda: NOW,
        )
        self.channel_id = uuid4()
        self.owner_id = uuid4()
        self.provider_id = "0012345678"
        self.state = RichMenuChannelState.objects.create(
            channel_public_id=self.channel_id,
            last_observation_kind="managed_default",
            last_observation_fingerprint="d" * 64,
            last_observed_at=NOW,
        )
        origin = RichMenuOperation.objects.create(
            operation_id=uuid4(),
            channel_state=self.state,
            owner_identity_public_id=self.owner_id,
            provider_id=self.provider_id,
            kind="apply",
            request_fingerprint="e" * 64,
            expected_channel_revision=NOW,
            status="succeeded",
            stage="verifying",
            result_code="succeeded",
            accepted_at=NOW,
            completed_at=NOW,
        )
        self.resource = ManagedRichMenu.objects.create(
            channel_state=self.state,
            origin_operation=origin,
            ownership_marker="lrm:v1:" + uuid4().hex,
            lifecycle="applied",
            image_digest="f" * 64,
            line_rich_menu_id="private-line-id",
        )
        self.state.current_resource = self.resource
        self.state.save(update_fields=("current_resource",))

    # テストケース: disable assessment証明からunlink受付を予約する。
    # 期待値: lock下で現在対象を選び、一件のunlink operationへ原子的に結び付ける。
    def test_reservation_selects_current_owned_target_atomically(self):
        command = self._command()

        result = self.repository.reserve_disable_unlink(command)

        self.assertIsInstance(result, ReservedDisableUnlink)
        self.assertEqual(result.target_resource_id, self.resource.public_id)
        operation = RichMenuOperation.objects.get(pk=command.deactivation_operation_id)
        self.assertEqual(operation.target_resource_id, self.resource.public_id)
        self.assertEqual(operation.kind, OperationKind.UNLINK.value)

    # テストケース: assessment後に観測、対象、所有権、revision proofを差し替える。
    # 期待値: operationを作らずstaleまたは外部変更のclosed rejectionになる。
    def test_reservation_rejects_stale_or_replaced_assessment_without_operation(self):
        command = self._command()
        self.state.last_observation_fingerprint = "0" * 64
        self.state.save(update_fields=("last_observation_fingerprint",))

        result = self.repository.reserve_disable_unlink(command)

        self.assertIsInstance(result, DisableUnlinkRejected)
        self.assertEqual(result.reason, "stale_assessment")
        self.assertFalse(
            RichMenuOperation.objects.filter(pk=command.deactivation_operation_id).exists()
        )

    # テストケース: assessment後にownership markerを差し替える。
    # 期待値: proof不一致となり、LINE境界を持たない予約層でoperation受付前に拒否する。
    def test_reservation_rejects_ownership_marker_replacement_before_line_io(self):
        command = self._command()
        self.resource.ownership_marker = "lrm:v1:" + uuid4().hex
        self.resource.save(update_fields=("ownership_marker",))

        result = self.repository.reserve_disable_unlink(command)

        self.assertEqual(result, DisableUnlinkRejected("stale_assessment"))
        self.assertFalse(
            RichMenuOperation.objects.filter(pk=command.deactivation_operation_id).exists()
        )

    # テストケース: assessment後にcurrent resourceを別の管理対象へ差し替える。
    # 期待値: 古いproofでは新対象を受付せず、operationもLINE callも発生しない。
    def test_reservation_rejects_current_resource_replacement_before_line_io(self):
        command = self._command()
        replacement_origin = RichMenuOperation.objects.create(
            operation_id=uuid4(),
            channel_state=self.state,
            owner_identity_public_id=self.owner_id,
            provider_id=self.provider_id,
            kind="apply",
            request_fingerprint="1" * 64,
            expected_channel_revision=NOW,
            status="succeeded",
            stage="verifying",
            result_code="succeeded",
            accepted_at=NOW,
            completed_at=NOW,
        )
        replacement = ManagedRichMenu.objects.create(
            channel_state=self.state,
            origin_operation=replacement_origin,
            ownership_marker="lrm:v1:" + uuid4().hex,
            lifecycle="applied",
            image_digest="2" * 64,
            line_rich_menu_id="replacement-private-id",
        )
        self.state.current_resource = replacement
        self.state.save(update_fields=("current_resource",))

        result = self.repository.reserve_disable_unlink(command)

        self.assertEqual(result, DisableUnlinkRejected("stale_assessment"))
        self.assertFalse(
            RichMenuOperation.objects.filter(pk=command.deactivation_operation_id).exists()
        )

    # テストケース: 予約前に外部既定、結果不明、後片付け待ちへ変化する。
    # 期待値: 各closed reasonで外部作用前に拒否しunlink operationを一件も作らない。
    def test_reservation_rejects_external_unknown_and_cleanup_states_before_line_io(self):
        cases = (
            ("external_default", "external_default"),
            ("unknown", "recheck_required"),
            ("cleanup", "cleanup_required"),
        )
        for state_kind, expected in cases:
            with self.subTest(state_kind=state_kind):
                command = self._command()
                if state_kind == "cleanup":
                    self.resource.lifecycle = "cleanup_required"
                    self.resource.save(update_fields=("lifecycle",))
                else:
                    self.state.last_observation_kind = state_kind
                    self.state.save(update_fields=("last_observation_kind",))
                result = self.repository.reserve_disable_unlink(command)
                self.assertEqual(result, DisableUnlinkRejected(expected))
                self.assertFalse(
                    RichMenuOperation.objects.filter(pk=command.deactivation_operation_id).exists()
                )
                self.resource.lifecycle = "applied"
                self.resource.save(update_fields=("lifecycle",))
                self.state.last_observation_kind = "managed_default"
                self.state.save(update_fields=("last_observation_kind",))

    def _command(self):
        proof = disable_assessment_proof(
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
            resource_id=self.resource.public_id,
            observation_fingerprint="d" * 64,
            ownership_marker=self.resource.ownership_marker,
        )
        return ReserveDisableUnlink(
            owner_identity_public_id=self.owner_id,
            provider_id=self.provider_id,
            channel_public_id=self.channel_id,
            expected_channel_revision=NOW,
            deactivation_operation_id=uuid4(),
            assessment_proof=proof,
        )
