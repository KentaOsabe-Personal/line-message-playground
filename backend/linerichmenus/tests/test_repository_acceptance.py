from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from django.test import TransactionTestCase

from linechannels.reference_fence import ReferenceFenceResult
from linerichmenus.models import ManagedRichMenu, RichMenuOperation
from linerichmenus.repository import (
    AcceptedOperation,
    DjangoRichMenuRepository,
    OperationAccepted,
    OperationConflict,
    OperationReplay,
)
from linerichmenus.types import OperationKind, OperationStatus


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class LockedFence:
    def lock_existing(self, channel_public_id):
        return ReferenceFenceResult("locked")


class RichMenuRepositoryAcceptanceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.repository = DjangoRichMenuRepository(
            reference_fence=LockedFence(), clock=lambda: NOW
        )
        self.command = AcceptedOperation(
            operation_id=uuid4(),
            channel_public_id=uuid4(),
            owner_identity_public_id=uuid4(),
            provider_id="0012345678",
            expected_channel_revision=NOW,
            kind=OperationKind.APPLY,
            subject_operation_id=None,
            target_resource_id=None,
            request_fingerprint="a" * 64,
            confirmation_usage_digest="b" * 64,
            configuration_snapshot={
                "version": 1,
                "templateId": "jp-link-one",
                "templateVersion": 1,
                "fields": [{"displayName": "例", "uri": "https://example.com/"}],
            },
            candidate_image_digest="c" * 64,
        )

    # テストケース: apply operationを初回受付する。
    # 期待値: operation・channel state・ownership marker付きcandidateが一transactionで一件ずつ予約される。
    def test_accept_reserves_one_operation_and_candidate(self):
        result = self.repository.accept(self.command)

        self.assertIsInstance(result, OperationAccepted)
        self.assertEqual(result.operation.status, OperationStatus.ACCEPTED)
        operation = RichMenuOperation.objects.get(pk=self.command.operation_id)
        candidate = ManagedRichMenu.objects.get(origin_operation=operation)
        self.assertEqual(candidate.lifecycle, "candidate")
        self.assertEqual(candidate.image_digest, "c" * 64)
        self.assertGreaterEqual(len(candidate.ownership_marker), 32)

    # テストケース: 同じglobal operation IDとfingerprintを再送する。
    # 期待値: 保存済み状態を返しoperationとcandidateを増やさない。
    def test_same_id_and_fingerprint_replays_saved_state(self):
        self.repository.accept(self.command)
        replay = self.repository.accept(self.command)

        self.assertIsInstance(replay, OperationReplay)
        self.assertEqual(replay.operation.operation_id, self.command.operation_id)
        self.assertEqual(RichMenuOperation.objects.count(), 1)
        self.assertEqual(ManagedRichMenu.objects.count(), 1)

    # テストケース: 同じoperation IDを異なるfingerprintで再利用する。
    # 期待値: 外部作用前のconflictとなり保存済み行を変更しない。
    def test_same_id_with_different_fingerprint_conflicts(self):
        self.repository.accept(self.command)
        conflict = self.repository.accept(
            replace(self.command, request_fingerprint="d" * 64)
        )

        self.assertIsInstance(conflict, OperationConflict)
        self.assertEqual(conflict.reason, "operation_conflict")
        self.assertEqual(RichMenuOperation.objects.count(), 1)

    # テストケース: 使用済みconfirmationを別operation IDへ流用する。
    # 期待値: usage digest一意性により別candidateを作らず拒否する。
    def test_confirmation_usage_is_atomic_and_unique(self):
        self.repository.accept(self.command)
        conflict = self.repository.accept(
            replace(
                self.command,
                operation_id=uuid4(),
                request_fingerprint="e" * 64,
            )
        )

        self.assertIsInstance(conflict, OperationConflict)
        self.assertEqual(conflict.reason, "confirmation_used")
        self.assertEqual(RichMenuOperation.objects.count(), 1)
        self.assertEqual(ManagedRichMenu.objects.count(), 1)

    # テストケース: unresolved active operationがあるchannelへ別の通常操作を受付する。
    # 期待値: channel単位競合として二件目を予約しない。
    def test_conflicting_channel_operation_is_rejected(self):
        self.repository.accept(self.command)
        conflict = self.repository.accept(
            replace(
                self.command,
                operation_id=uuid4(),
                request_fingerprint="f" * 64,
                confirmation_usage_digest="1" * 64,
            )
        )

        self.assertIsInstance(conflict, OperationConflict)
        self.assertEqual(conflict.reason, "operation_in_progress")
        self.assertEqual(RichMenuOperation.objects.count(), 1)

    # テストケース: applied resourceを対象にUNLINKとRELEASEを受付する。
    # 期待値: targetを同一channelでlock検証してoperation relationへ永続化する。
    def test_unlink_and_release_persist_locked_target_resource(self):
        accepted = self.repository.accept(self.command)
        resource = ManagedRichMenu.objects.get(pk=accepted.candidate_resource_id)
        resource.lifecycle = "applied"
        resource.save(update_fields=("lifecycle",))
        state = resource.channel_state
        state.active_operation = None
        state.current_resource = resource
        state.save(update_fields=("active_operation", "current_resource"))

        unlink = AcceptedOperation(
            operation_id=uuid4(), channel_public_id=self.command.channel_public_id,
            owner_identity_public_id=self.command.owner_identity_public_id,
            provider_id=self.command.provider_id, expected_channel_revision=NOW,
            kind=OperationKind.UNLINK, subject_operation_id=None,
            target_resource_id=resource.public_id, request_fingerprint="2" * 64,
        )
        result = self.repository.accept(unlink)
        self.assertIsInstance(result, OperationAccepted)
        self.assertEqual(
            RichMenuOperation.objects.get(pk=unlink.operation_id).target_resource_id,
            resource.public_id,
        )

        state.active_operation = None
        state.save(update_fields=("active_operation",))
        release = replace(
            unlink, operation_id=uuid4(), kind=OperationKind.RELEASE,
            request_fingerprint="3" * 64,
        )
        result = self.repository.accept(release)
        self.assertIsInstance(result, OperationAccepted)
        self.assertEqual(
            RichMenuOperation.objects.get(pk=release.operation_id).target_resource_id,
            resource.public_id,
        )

    # テストケース: 別channelまたは非applied resourceをUNLINK対象にする。
    # 期待値: operation rowを作らずinvalid relationで拒否する。
    def test_unlink_rejects_unrelated_target(self):
        command = AcceptedOperation(
            operation_id=uuid4(), channel_public_id=self.command.channel_public_id,
            owner_identity_public_id=self.command.owner_identity_public_id,
            provider_id=self.command.provider_id, expected_channel_revision=NOW,
            kind=OperationKind.UNLINK, subject_operation_id=None,
            target_resource_id=uuid4(), request_fingerprint="4" * 64,
        )
        result = self.repository.accept(command)
        self.assertIsInstance(result, OperationConflict)
        self.assertEqual(result.reason, "invalid_relation")
