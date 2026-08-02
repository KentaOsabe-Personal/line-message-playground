from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from django.test import TransactionTestCase

from linechannels.reference_fence import ReferenceFenceResult
from linerichmenus.models import RichMenuOperation
from linerichmenus.repository import (
    AcceptedOperation,
    DjangoRichMenuRepository,
    OperationFenceResult,
    StageClaimed,
    StageConflict,
    StageOutcome,
)
from linerichmenus.types import OperationKind, OperationStage, OperationStatus, SafeResultCode


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class LockedFence:
    def lock_existing(self, channel_public_id):
        return ReferenceFenceResult("locked")


class ExactOperationFence:
    def __init__(self, status="matched"):
        self.status = status
        self.calls = []

    def lock_exact(self, snapshot):
        self.calls.append(snapshot)
        return OperationFenceResult(self.status)


class RichMenuRepositoryStageCASTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.fence = ExactOperationFence()
        self.repository = DjangoRichMenuRepository(
            reference_fence=LockedFence(), operation_fence=self.fence, clock=lambda: NOW
        )
        self.command = AcceptedOperation(
            operation_id=uuid4(), channel_public_id=uuid4(),
            owner_identity_public_id=uuid4(), provider_id="0012345678",
            expected_channel_revision=NOW, kind=OperationKind.APPLY,
            subject_operation_id=None, target_resource_id=None,
            request_fingerprint="a" * 64, confirmation_usage_digest="b" * 64,
            configuration_snapshot={"version": 1, "templateId": "jp-link-one", "templateVersion": 1, "fields": []},
            candidate_image_digest="c" * 64,
        )
        self.repository.accept(self.command)

    # テストケース: accepted operationのcreating stageをclaimする。
    # 期待値: processing/creatingと開始時刻をCAS保存し、DB lockを保持し続けない。
    def test_claim_stage_marks_exact_stage_in_flight(self):
        result = self.repository.claim_stage(self.command.operation_id, OperationStage.CREATING)

        self.assertIsInstance(result, StageClaimed)
        stored = RichMenuOperation.objects.get(pk=self.command.operation_id)
        self.assertEqual(stored.status, "processing")
        self.assertEqual(stored.stage, "creating")
        self.assertEqual(stored.stage_started_at, NOW)

    # テストケース: claim済みstageを同じ要求で二重claimする。
    # 期待値: 外部作用を再実行可能にせずstage conflictを返す。
    def test_in_flight_stage_cannot_be_claimed_twice(self):
        self.repository.claim_stage(self.command.operation_id, OperationStage.CREATING)
        result = self.repository.claim_stage(self.command.operation_id, OperationStage.CREATING)
        self.assertIsInstance(result, StageConflict)
        self.assertEqual(result.reason, "stage_in_flight")

    # テストケース: process crash後にin-flight期限を超過したstageをclaimする。
    # 期待値: 同じ外部作用を再claimせずunknown blockerへCASしてrecheckを要求する。
    def test_expired_in_flight_stage_converges_to_unknown(self):
        self.repository.claim_stage(self.command.operation_id, OperationStage.CREATING)
        self.repository._clock = lambda: NOW + timedelta(minutes=6)
        result = self.repository.claim_stage(self.command.operation_id, OperationStage.CREATING)

        self.assertEqual(result.operation.status, OperationStatus.UNKNOWN)
        stored = RichMenuOperation.objects.get(pk=self.command.operation_id)
        self.assertEqual(stored.status, "unknown")
        self.assertIsNone(stored.stage_started_at)
        self.assertEqual(stored.channel_state.blocking_operation_id, stored.operation_id)

    # テストケース: 外部I/O後に全fence軸が一致した結果を完了する。
    # 期待値: creatingから次のuploading ready stateへ一意に進む。
    def test_complete_stage_uses_exact_fence_and_advances(self):
        self.repository.claim_stage(self.command.operation_id, OperationStage.CREATING)
        result = self.repository.complete_stage(self._stage_outcome())

        self.assertEqual(result.status, OperationStatus.PROCESSING)
        self.assertEqual(result.stage, OperationStage.UPLOADING)
        stored = RichMenuOperation.objects.get(pk=self.command.operation_id)
        self.assertIsNone(stored.stage_started_at)
        self.assertEqual(len(self.fence.calls), 1)
        self.assertEqual(self.fence.calls[0].provider_id, "0012345678")

    # テストケース: 外部I/O中にowner/provider/channel revision fenceが変わる。
    # 期待値: 外部結果を確定せずunknown blockerへ収束する。
    def test_stale_operation_fence_converges_to_unknown(self):
        self.repository.claim_stage(self.command.operation_id, OperationStage.CREATING)
        self.fence.status = "stale"
        result = self.repository.complete_stage(self._stage_outcome())

        self.assertEqual(result.status, OperationStatus.UNKNOWN)
        self.assertEqual(result.stage, OperationStage.CREATING)
        stored = RichMenuOperation.objects.get(pk=self.command.operation_id)
        self.assertEqual(stored.result_code, "response_unknown")
        self.assertEqual(stored.channel_state.blocking_operation_id, stored.operation_id)

    # テストケース: stageが既に先へ進んだ後に古いcreating応答が戻る。
    # 期待値: 現在stageを上書きせずCAS conflictを返す。
    def test_late_response_does_not_overwrite_current_stage(self):
        self.repository.claim_stage(self.command.operation_id, OperationStage.CREATING)
        self.repository.complete_stage(self._stage_outcome())
        late = self.repository.complete_stage(self._stage_outcome())

        self.assertIsInstance(late, StageConflict)
        self.assertEqual(late.reason, "stale_stage")
        stored = RichMenuOperation.objects.get(pk=self.command.operation_id)
        self.assertEqual(stored.stage, "uploading")

    # テストケース: 各外部I/O stage中にowner/provider/channel revision fenceが変わる。
    # 期待値: create/upload/set/observe/clear/deleteの旧応答を採用せず元stageのunknownへ収束する。
    def test_stale_fence_discards_late_response_for_every_external_stage(self):
        for stage in (
            OperationStage.CREATING,
            OperationStage.UPLOADING,
            OperationStage.SETTING_DEFAULT,
            OperationStage.VERIFYING,
            OperationStage.CLEARING_DEFAULT,
            OperationStage.CLEANING,
        ):
            with self.subTest(stage=stage):
                operation = RichMenuOperation.objects.get(pk=self.command.operation_id)
                RichMenuOperation.objects.filter(pk=operation.pk).update(
                    status="processing",
                    stage=stage.value,
                    stage_started_at=NOW,
                    result_code="accepted",
                    completed_at=None,
                )
                operation.channel_state.active_operation_id = operation.operation_id
                operation.channel_state.blocking_operation = None
                operation.channel_state.save(
                    update_fields=("active_operation", "blocking_operation", "updated_at")
                )
                self.fence.status = "stale"

                result = self.repository.complete_stage(
                    StageOutcome(
                        operation_id=operation.operation_id,
                        expected_stage=stage,
                        next_status=OperationStatus.SUCCEEDED,
                        next_stage=stage,
                        result=SafeResultCode.SUCCEEDED,
                    )
                )

                self.assertEqual(result.status, OperationStatus.UNKNOWN)
                self.assertEqual(result.stage, stage)
                stored = RichMenuOperation.objects.get(pk=operation.pk)
                self.assertEqual(stored.channel_state.blocking_operation_id, stored.operation_id)

    def _stage_outcome(self):
        return StageOutcome(
            operation_id=self.command.operation_id,
            expected_stage=OperationStage.CREATING,
            next_status=OperationStatus.PROCESSING,
            next_stage=OperationStage.UPLOADING,
            result=SafeResultCode.ACCEPTED,
        )
