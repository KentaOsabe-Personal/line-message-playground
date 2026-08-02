from datetime import UTC, datetime
from uuid import uuid4

from django.test import TransactionTestCase

from linerichmenus.models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation
from linerichmenus.repository import (
    DjangoRichMenuRepository,
    OperationConflict,
    OperationFenceResult,
    ReplacementRecorded,
)


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class MutableOperationFence:
    def __init__(self):
        self.status = "matched"
        self.calls = []

    def lock_exact(self, snapshot):
        self.calls.append(snapshot)
        return OperationFenceResult(self.status)


class RichMenuRepositoryReplacementTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.state = RichMenuChannelState.objects.create(channel_public_id=uuid4())
        self.previous = self._operation(status="succeeded", stage="verifying")
        self.old = ManagedRichMenu.objects.create(
            channel_state=self.state, origin_operation=self.previous,
            ownership_marker="old-" + uuid4().hex,
            lifecycle="applied", image_digest="a" * 64,
        )
        self.replacement = self._operation(status="processing", stage="verifying")
        self.candidate = ManagedRichMenu.objects.create(
            channel_state=self.state, origin_operation=self.replacement,
            ownership_marker="new-" + uuid4().hex,
            lifecycle="candidate", image_digest="b" * 64,
        )
        self.state.current_resource = self.old
        self.state.active_operation = self.replacement
        self.state.save(update_fields=("current_resource", "active_operation"))
        self.operation_fence = MutableOperationFence()
        self.repository = DjangoRichMenuRepository(operation_fence=self.operation_fence)

    # テストケース: replacement確認時に新旧resourceとchannel stateを更新する。
    # 期待値: new applied、old old、old.replacement operation、current newがatomicに保存される。
    def test_record_replacement_persists_unique_causality_atomically(self):
        result = self.repository.record_replacement(
            replacement_operation_id=self.replacement.operation_id,
            new_resource_id=self.candidate.public_id,
            old_resource_id=self.old.public_id,
        )
        self.assertIsInstance(result, ReplacementRecorded)
        self.candidate.refresh_from_db()
        self.old.refresh_from_db()
        self.state.refresh_from_db()
        self.assertEqual(self.candidate.lifecycle, "applied")
        self.assertEqual(self.old.lifecycle, "old")
        self.assertEqual(self.old.replacement_operation_id, self.replacement.operation_id)
        self.assertEqual(self.state.current_resource_id, self.candidate.public_id)
        self.assertEqual(len(self.operation_fence.calls), 1)
        self.assertEqual(
            self.operation_fence.calls[0].expected_channel_revision,
            self.replacement.expected_channel_revision,
        )

    # テストケース: default一致観測後、置換確定前にchannel revisionが変わる。
    # 期待値: exact fenceで拒否し、新旧resource・relation・current pointerを一切変更しない。
    def test_record_replacement_rejects_stale_observation_without_mutation(self):
        self.operation_fence.status = "stale"

        result = self.repository.record_replacement(
            replacement_operation_id=self.replacement.operation_id,
            new_resource_id=self.candidate.public_id,
            old_resource_id=self.old.public_id,
        )

        self.assertIsInstance(result, OperationConflict)
        self.assertEqual(result.reason, "stale_channel")
        self.candidate.refresh_from_db()
        self.old.refresh_from_db()
        self.state.refresh_from_db()
        self.assertEqual(self.candidate.lifecycle, "candidate")
        self.assertEqual(self.old.lifecycle, "applied")
        self.assertIsNone(self.old.replacement_operation_id)
        self.assertEqual(self.state.current_resource_id, self.old.public_id)

    # テストケース: currentでない旧resourceまたは別operation candidateを置換記録へ渡す。
    # 期待値: 部分更新せずinvalid relationとして拒否する。
    def test_record_replacement_rejects_unrelated_resources(self):
        unrelated = ManagedRichMenu.objects.create(
            channel_state=self.state, origin_operation=self.previous,
            ownership_marker="unrelated-" + uuid4().hex,
            lifecycle="applied", image_digest="c" * 64,
        )
        result = self.repository.record_replacement(
            replacement_operation_id=self.replacement.operation_id,
            new_resource_id=self.candidate.public_id,
            old_resource_id=unrelated.public_id,
        )
        self.assertIsInstance(result, OperationConflict)
        self.assertEqual(result.reason, "invalid_relation")
        self.candidate.refresh_from_db()
        self.old.refresh_from_db()
        self.assertEqual(self.candidate.lifecycle, "candidate")
        self.assertEqual(self.old.lifecycle, "applied")

    def _operation(self, *, status, stage):
        return RichMenuOperation.objects.create(
            operation_id=uuid4(), channel_state=self.state,
            owner_identity_public_id=uuid4(), provider_id="0012345678",
            kind="apply", request_fingerprint=uuid4().hex * 2,
            confirmation_usage_digest=uuid4().hex * 2,
            expected_channel_revision=NOW, status=status, stage=stage,
            result_code="accepted" if status == "processing" else "succeeded",
            accepted_at=NOW, completed_at=NOW if status == "succeeded" else None,
        )
