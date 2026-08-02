from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from django.test import TransactionTestCase

from linechannels.reference_fence import ReferenceFenceResult
from linerichmenus.models import RichMenuOperation
from linerichmenus.repository import (
    AcceptedOperation,
    DjangoRichMenuRepository,
    OperationConflict,
    OperationFenceResult,
    RecoveryAccepted,
    RecoveryOutcome,
    StageExpired,
    StageOutcome,
)
from linerichmenus.types import OperationKind, OperationStage, OperationStatus, SafeResultCode


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class LockedFence:
    def lock_existing(self, channel_public_id):
        return ReferenceFenceResult("locked")


class MutableOperationFence:
    status = "matched"

    def __init__(self):
        self.calls = []

    def lock_exact(self, snapshot):
        self.calls.append(snapshot)
        return OperationFenceResult(self.status)


class RichMenuRepositoryRecoveryTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.operation_fence = MutableOperationFence()
        self.repository = DjangoRichMenuRepository(
            reference_fence=LockedFence(), operation_fence=self.operation_fence, clock=lambda: NOW
        )
        self.apply = AcceptedOperation(
            operation_id=uuid4(), channel_public_id=uuid4(), owner_identity_public_id=uuid4(),
            provider_id="0012345678", expected_channel_revision=NOW, kind=OperationKind.APPLY,
            subject_operation_id=None, target_resource_id=None, request_fingerprint="a" * 64,
            confirmation_usage_digest="b" * 64,
            configuration_snapshot={"version": 1, "templateId": "jp-link-one", "templateVersion": 1, "fields": []},
            candidate_image_digest="c" * 64,
        )
        accepted = self.repository.accept(self.apply)
        self.candidate_id = accepted.candidate_resource_id
        self.repository.claim_stage(self.apply.operation_id, OperationStage.CREATING)
        self.operation_fence.status = "stale"
        self.repository.complete_stage(
            StageOutcome(
                operation_id=self.apply.operation_id, expected_stage=OperationStage.CREATING,
                next_status=OperationStatus.PROCESSING, next_stage=OperationStage.UPLOADING,
                result=SafeResultCode.ACCEPTED,
            )
        )
        self.operation_fence.status = "matched"
        self.recheck = AcceptedOperation(
            operation_id=uuid4(), channel_public_id=self.apply.channel_public_id,
            owner_identity_public_id=self.apply.owner_identity_public_id,
            provider_id=self.apply.provider_id, expected_channel_revision=NOW,
            kind=OperationKind.RECHECK, subject_operation_id=self.apply.operation_id,
            target_resource_id=None, request_fingerprint="d" * 64,
        )

    # テストケース: 現在blockerをsubjectに持つrecheckを受付する。
    # 期待値: blockerを維持したままrecoveryだけがactiveへatomic claimされる。
    def test_accept_recovery_separates_blocker_and_active_operation(self):
        accepted = self.repository.accept_recovery(self.recheck)

        self.assertIsInstance(accepted, RecoveryAccepted)
        state = RichMenuOperation.objects.get(pk=self.apply.operation_id).channel_state
        self.assertEqual(state.blocking_operation_id, self.apply.operation_id)
        self.assertEqual(state.active_operation_id, self.recheck.operation_id)
        self.assertEqual(accepted.operation.status, OperationStatus.RECOVERY_ACTIVE)
        self.assertEqual(accepted.operation.stage, OperationStage.VERIFYING)

    # テストケース: subjectが旧revisionでunknownになった後、現在revisionでrecheckを受付する。
    # 期待値: childへ現在revisionを独立bindし、受付時exact fenceを検証する。
    def test_recovery_binds_current_revision_independently_from_subject(self):
        current_revision = NOW + timedelta(minutes=1)
        command = replace(self.recheck, expected_channel_revision=current_revision)
        self.operation_fence.calls.clear()

        accepted = self.repository.accept_recovery(command)

        self.assertIsInstance(accepted, RecoveryAccepted)
        stored = RichMenuOperation.objects.get(pk=command.operation_id)
        self.assertEqual(stored.expected_channel_revision, current_revision)
        self.assertEqual(self.operation_fence.calls[-1].expected_channel_revision, current_revision)

    # テストケース: recovery受付時のcurrent revision fenceがstaleである。
    # 期待値: childを作成せずstale channelとして拒否する。
    def test_recovery_rejects_stale_current_revision_at_acceptance(self):
        self.operation_fence.status = "stale"
        rejected = self.repository.accept_recovery(self.recheck)
        self.assertIsInstance(rejected, OperationConflict)
        self.assertEqual(rejected.reason, "stale_channel")
        self.assertFalse(RichMenuOperation.objects.filter(pk=self.recheck.operation_id).exists())

    # テストケース: recovery active中に二件目または異subjectのrecoveryを受付する。
    # 期待値: 二重recoveryと異subjectを外部作用前に拒否する。
    def test_double_or_wrong_subject_recovery_is_rejected(self):
        self.repository.accept_recovery(self.recheck)
        second = self.repository.accept_recovery(
            replace(self.recheck, operation_id=uuid4(), request_fingerprint="e" * 64)
        )
        wrong = self.repository.accept_recovery(
            replace(
                self.recheck, operation_id=uuid4(), subject_operation_id=uuid4(),
                request_fingerprint="f" * 64,
            )
        )
        self.assertIsInstance(second, OperationConflict)
        self.assertEqual(second.reason, "operation_in_progress")
        self.assertIsInstance(wrong, OperationConflict)

    # テストケース: unknown blockerへcleanup、または別operation由来candidateへcleanupを要求する。
    # 期待値: kind/subject stage/target originの許可行列により拒否する。
    def test_cleanup_requires_cleanup_blocker_and_related_target(self):
        cleanup_for_unknown = replace(
            self.recheck, operation_id=uuid4(), kind=OperationKind.CLEANUP,
            target_resource_id=self.candidate_id, request_fingerprint="2" * 64,
        )
        rejected = self.repository.accept_recovery(cleanup_for_unknown)
        self.assertIsInstance(rejected, OperationConflict)
        self.assertEqual(rejected.reason, "invalid_relation")

    # テストケース: cleanup delete unknown operationをsubjectにcleanupを再受付する。
    # 期待値: deleteを再実行可能にせずrecheckだけを許可する。
    def test_unknown_cleanup_operation_cannot_spawn_another_cleanup(self):
        stored = RichMenuOperation.objects.get(pk=self.apply.operation_id)
        stored.status = "cleanup_required"
        stored.stage = "cleaning"
        stored.save(update_fields=("status", "stage"))
        cleanup = replace(
            self.recheck, operation_id=uuid4(), kind=OperationKind.CLEANUP,
            target_resource_id=self.candidate_id, request_fingerprint="3" * 64,
        )
        self.repository.accept_recovery(cleanup)
        self.repository.handoff_recovery(
            RecoveryOutcome(
                recovery_operation_id=cleanup.operation_id,
                subject_operation_id=self.apply.operation_id,
                subject_next_status=OperationStatus.CLEANUP_REQUIRED,
                subject_next_stage=OperationStage.CLEANING,
                subject_result=SafeResultCode.CLEANUP_REQUIRED,
                blocker_moves_to_recovery=True,
            )
        )
        second_cleanup = replace(
            cleanup, operation_id=uuid4(), subject_operation_id=cleanup.operation_id,
            request_fingerprint="4" * 64,
        )
        rejected = self.repository.accept_recovery(second_cleanup)
        self.assertIsInstance(rejected, OperationConflict)
        self.assertEqual(rejected.reason, "invalid_relation")

    # テストケース: cleanup delete unknown handoffでsubjectを任意stageへ改変する。
    # 期待値: cleanup_required/cleaningの固定形以外を保存前に拒否する。
    def test_cleanup_unknown_handoff_rejects_arbitrary_subject_state(self):
        stored = RichMenuOperation.objects.get(pk=self.apply.operation_id)
        stored.status = "cleanup_required"
        stored.stage = "cleaning"
        stored.save(update_fields=("status", "stage"))
        cleanup = replace(
            self.recheck, operation_id=uuid4(), kind=OperationKind.CLEANUP,
            target_resource_id=self.candidate_id, request_fingerprint="5" * 64,
        )
        self.repository.accept_recovery(cleanup)
        result = self.repository.handoff_recovery(
            RecoveryOutcome(
                recovery_operation_id=cleanup.operation_id,
                subject_operation_id=self.apply.operation_id,
                subject_next_status=OperationStatus.SUCCEEDED,
                subject_next_stage=OperationStage.LOCAL_RELEASE,
                subject_result=SafeResultCode.SUCCEEDED,
                blocker_moves_to_recovery=True,
            )
        )
        self.assertEqual(result.reason, "invalid_transition")
        stored.refresh_from_db()
        self.assertEqual(stored.status, "cleanup_required")
        self.assertEqual(stored.stage, "cleaning")

    # テストケース: replacement APPLYがcurrent resourceを所有し旧managed resourceをcleanupする。
    # 期待値: current originとsubjectの一致を強い関係としてold targetを受付できる。
    def test_replacement_subject_can_cleanup_old_managed_resource(self):
        subject = RichMenuOperation.objects.get(pk=self.apply.operation_id)
        subject.status = "cleanup_required"
        subject.stage = "cleaning"
        subject.save(update_fields=("status", "stage"))
        current = subject.originated_resources.get(pk=self.candidate_id)
        current.lifecycle = "applied"
        current.save(update_fields=("lifecycle",))
        subject.channel_state.current_resource = current
        subject.channel_state.save(update_fields=("current_resource",))
        previous = RichMenuOperation.objects.create(
            operation_id=uuid4(), channel_state=subject.channel_state,
            owner_identity_public_id=self.apply.owner_identity_public_id,
            provider_id=self.apply.provider_id, kind="apply",
            request_fingerprint="6" * 64, confirmation_usage_digest="7" * 64,
            expected_channel_revision=NOW, status="succeeded", stage="verifying",
            result_code="succeeded", accepted_at=NOW, completed_at=NOW,
        )
        from linerichmenus.models import ManagedRichMenu
        old = ManagedRichMenu.objects.create(
            channel_state=subject.channel_state, origin_operation=previous,
            replacement_operation=subject,
            ownership_marker="lrm:v1:" + uuid4().hex,
            lifecycle="old", image_digest="8" * 64,
        )
        cleanup = replace(
            self.recheck, operation_id=uuid4(), kind=OperationKind.CLEANUP,
            target_resource_id=old.public_id, request_fingerprint="9" * 64,
        )
        accepted = self.repository.accept_recovery(cleanup)
        self.assertIsInstance(accepted, RecoveryAccepted)

    # テストケース: 同一channelのrelation不明なlegacy oldをcleanup対象にする。
    # 期待値: current resource推論を使わずfail closedに拒否する。
    def test_cleanup_rejects_old_resource_without_replacement_relation(self):
        subject = RichMenuOperation.objects.get(pk=self.apply.operation_id)
        subject.status = "cleanup_required"
        subject.stage = "cleaning"
        subject.save(update_fields=("status", "stage"))
        previous = RichMenuOperation.objects.create(
            operation_id=uuid4(), channel_state=subject.channel_state,
            owner_identity_public_id=self.apply.owner_identity_public_id,
            provider_id=self.apply.provider_id, kind="apply",
            request_fingerprint="0" * 64, confirmation_usage_digest="1" * 64,
            expected_channel_revision=NOW, status="succeeded", stage="verifying",
            result_code="succeeded", accepted_at=NOW, completed_at=NOW,
        )
        from linerichmenus.models import ManagedRichMenu
        legacy_old = ManagedRichMenu.objects.create(
            channel_state=subject.channel_state, origin_operation=previous,
            ownership_marker="legacy-old-" + uuid4().hex,
            lifecycle="old", image_digest="2" * 64,
        )
        cleanup = replace(
            self.recheck, operation_id=uuid4(), kind=OperationKind.CLEANUP,
            target_resource_id=legacy_old.public_id, request_fingerprint="3" * 64,
        )
        rejected = self.repository.accept_recovery(cleanup)
        self.assertIsInstance(rejected, OperationConflict)
        self.assertEqual(rejected.reason, "invalid_relation")

    # テストケース: recheck観測が元creating作用の成功を確認する。
    # 期待値: childを完了しsubjectを未開始uploadingへhandoffしてcreatingを再claimしない。
    def test_successful_recovery_hands_subject_to_next_unstarted_stage(self):
        self.repository.accept_recovery(self.recheck)
        self.operation_fence.calls.clear()
        result = self.repository.handoff_recovery(
            RecoveryOutcome(
                recovery_operation_id=self.recheck.operation_id,
                subject_operation_id=self.apply.operation_id,
                subject_next_status=OperationStatus.PROCESSING,
                subject_next_stage=OperationStage.UPLOADING,
                subject_result=SafeResultCode.ACCEPTED,
                blocker_moves_to_recovery=False,
            )
        )

        self.assertEqual(result.subject.status, OperationStatus.PROCESSING)
        self.assertEqual(result.subject.stage, OperationStage.UPLOADING)
        self.assertEqual(result.recovery.status, OperationStatus.SUCCEEDED)
        state = RichMenuOperation.objects.get(pk=self.apply.operation_id).channel_state
        self.assertIsNone(state.blocking_operation_id)
        self.assertEqual(state.active_operation_id, self.apply.operation_id)
        self.assertIsNone(RichMenuOperation.objects.get(pk=self.apply.operation_id).stage_started_at)
        self.assertEqual(self.operation_fence.calls[-1].expected_channel_revision, NOW)

    # テストケース: recovery観測中にowner/provider/channel revision fenceが変わる。
    # 期待値: subject blockerを不変に保ちchildだけを安全終了してactiveを解放する。
    def test_stale_recovery_handoff_does_not_apply_observation(self):
        current_revision = NOW + timedelta(minutes=1)
        command = replace(self.recheck, expected_channel_revision=current_revision)
        self.repository.accept_recovery(command)
        self.operation_fence.status = "stale"

        result = self.repository.handoff_recovery(
            RecoveryOutcome(
                recovery_operation_id=command.operation_id,
                subject_operation_id=self.apply.operation_id,
                subject_next_status=OperationStatus.PROCESSING,
                subject_next_stage=OperationStage.UPLOADING,
                subject_result=SafeResultCode.ACCEPTED,
                blocker_moves_to_recovery=False,
            )
        )

        self.assertEqual(result.recovery.status, OperationStatus.FAILED)
        subject = RichMenuOperation.objects.get(pk=self.apply.operation_id)
        self.assertEqual(subject.status, "unknown")
        self.assertEqual(subject.stage, "creating")
        state = subject.channel_state
        self.assertEqual(state.blocking_operation_id, subject.operation_id)
        self.assertIsNone(state.active_operation_id)

        self.operation_fence.status = "matched"
        retry = replace(command, operation_id=uuid4(), request_fingerprint="4" * 64)
        self.assertIsInstance(self.repository.accept_recovery(retry), RecoveryAccepted)

    # テストケース: recheck観測中にworkerが停止しclaim期限を超過する。
    # 期待値: childだけをfailedへ収束し、元subjectをblockerとして維持する。
    def test_expired_recheck_preserves_subject_blocker(self):
        self.repository.accept_recovery(self.recheck)
        expiring_repository = DjangoRichMenuRepository(
            reference_fence=LockedFence(),
            operation_fence=self.operation_fence,
            clock=lambda: NOW + timedelta(minutes=6),
        )

        result = expiring_repository.claim_stage(
            self.recheck.operation_id, OperationStage.VERIFYING
        )

        self.assertIsInstance(result, StageExpired)
        self.assertEqual(result.operation.status, OperationStatus.FAILED)
        child = RichMenuOperation.objects.get(pk=self.recheck.operation_id)
        subject = RichMenuOperation.objects.get(pk=self.apply.operation_id)
        state = subject.channel_state
        self.assertEqual(child.result_code, SafeResultCode.RESPONSE_UNKNOWN.value)
        self.assertEqual(child.transitions.latest("sequence").from_status, "recovery_active")
        self.assertEqual(subject.status, OperationStatus.UNKNOWN.value)
        self.assertEqual(state.blocking_operation_id, subject.operation_id)
        self.assertIsNone(state.active_operation_id)

    # テストケース: cleanup delete観測中にworkerが停止しclaim期限を超過する。
    # 期待値: 外部delete作用を不明としてchildだけを新blockerへ移す。
    def test_expired_cleanup_moves_blocker_to_recovery_child(self):
        subject = RichMenuOperation.objects.get(pk=self.apply.operation_id)
        subject.status = OperationStatus.CLEANUP_REQUIRED.value
        subject.stage = OperationStage.CLEANING.value
        subject.save(update_fields=("status", "stage"))
        cleanup = replace(
            self.recheck,
            operation_id=uuid4(),
            kind=OperationKind.CLEANUP,
            target_resource_id=self.candidate_id,
            request_fingerprint="5" * 64,
        )
        self.repository.accept_recovery(cleanup)
        expiring_repository = DjangoRichMenuRepository(
            reference_fence=LockedFence(),
            operation_fence=self.operation_fence,
            clock=lambda: NOW + timedelta(minutes=6),
        )

        result = expiring_repository.claim_stage(
            cleanup.operation_id, OperationStage.CLEANING
        )

        self.assertIsInstance(result, StageExpired)
        self.assertEqual(result.operation.status, OperationStatus.UNKNOWN)
        child = RichMenuOperation.objects.get(pk=cleanup.operation_id)
        subject.refresh_from_db()
        state = subject.channel_state
        self.assertEqual(child.result_code, SafeResultCode.RESPONSE_UNKNOWN.value)
        self.assertEqual(child.transitions.latest("sequence").from_status, "recovery_active")
        self.assertEqual(subject.status, OperationStatus.CLEANUP_REQUIRED.value)
        self.assertEqual(state.blocking_operation_id, child.operation_id)
        self.assertIsNone(state.active_operation_id)

    # テストケース: cleanup deleteの結果だけがunknownとなる。
    # 期待値: cleanup childだけを新blockerへ移し元blockerを再実行対象にしない。
    def test_cleanup_unknown_moves_blocker_to_recovery_child(self):
        stored = RichMenuOperation.objects.get(pk=self.apply.operation_id)
        stored.status = "cleanup_required"
        stored.stage = "cleaning"
        stored.save(update_fields=("status", "stage"))
        cleanup = replace(
            self.recheck,
            operation_id=uuid4(), kind=OperationKind.CLEANUP,
            target_resource_id=self.candidate_id, request_fingerprint="1" * 64,
        )
        self.repository.accept_recovery(cleanup)
        result = self.repository.handoff_recovery(
            RecoveryOutcome(
                recovery_operation_id=cleanup.operation_id,
                subject_operation_id=self.apply.operation_id,
                subject_next_status=OperationStatus.CLEANUP_REQUIRED,
                subject_next_stage=OperationStage.CLEANING,
                subject_result=SafeResultCode.CLEANUP_REQUIRED,
                blocker_moves_to_recovery=True,
            )
        )
        self.assertEqual(result.recovery.status, OperationStatus.UNKNOWN)
        state = RichMenuOperation.objects.get(pk=self.apply.operation_id).channel_state
        self.assertEqual(state.blocking_operation_id, cleanup.operation_id)
        self.assertIsNone(state.active_operation_id)
