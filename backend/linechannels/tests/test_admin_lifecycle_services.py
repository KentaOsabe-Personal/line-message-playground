from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone

from lineaccounts.admin_authorization import OwnerActiveProof, OwnerOperationContext
from linechannels.admin_lifecycle_services import DefaultChannelDeactivationCoordinator
from linechannels.admin_lifecycle_types import (
    CompletedDeactivation,
    DeactivationView,
    LockedDeactivation,
    RecheckDeactivation,
    ReactivateChannel,
    ReservedDeactivation,
    SavedDeactivation,
    StartDeactivation,
)
from linerichmenus.headless import DisableAssessment, DisableRecoveryResult


class RecordingOwnerFence:
    def __init__(self, proof):
        self.proof = proof
        self.calls = []

    def lock_active(self, context, now):
        self.calls.append(transaction.get_connection().in_atomic_block)
        return self.proof


class ChannelDeactivationCoordinatorTests(TransactionTestCase):
    def setUp(self):
        self.owner = OwnerOperationContext(uuid4(), uuid4())
        self.proof = OwnerActiveProof(self.owner.identity_public_id, "0012345678")
        self.fence = RecordingOwnerFence(self.proof)
        self.repository = Mock()
        self.lifecycle = Mock()
        self.foundation = Mock()
        self.coordinator = DefaultChannelDeactivationCoordinator(
            self.fence, self.repository, self.lifecycle, self.foundation
        )
        self.channel_id = uuid4()
        self.operation_id = uuid4()
        self.revision = timezone.now()

    def view(self, **changes):
        values = dict(
            channel_public_id=self.channel_id,
            channel_active=True,
            channel_revision=self.revision,
            operation_id=self.operation_id,
            expected_channel_revision=self.revision,
            status="checking",
            safe_reason=None,
            subject_rich_operation_id=None,
            latest_recovery_operation_id=None,
            accepted_at=self.revision,
            updated_at=self.revision,
            completed_at=None,
        )
        values.update(changes)
        return DeactivationView(**values)

    def command(self):
        return StartDeactivation(self.channel_id, self.operation_id, self.revision)

    # 2.2 RED: clear評価は外部I/O中lockを保持せず原子的完了へ進む。
    def test_start_clear_completes_without_holding_database_lock(self):
        reserved = self.view()
        completed = self.view(
            channel_active=False, status="completed", completed_at=timezone.now()
        )
        self.repository.reserve.return_value = ReservedDeactivation(reserved)

        def assess(_command):
            self.assertFalse(transaction.get_connection().in_atomic_block)
            return DisableAssessment("clear_to_disable")

        self.lifecycle.assess_disable.side_effect = assess
        self.repository.complete_inactive.return_value = CompletedDeactivation(
            completed, completed.channel_revision
        )

        result = self.coordinator.start(self.owner, self.command())

        self.assertEqual(result.view.status, "completed")
        self.assertTrue(all(self.fence.calls))

    # 2.2 RED: managed defaultは同じdeactivation IDでunlinkし、成功後に再評価する。
    def test_start_unlink_uses_same_intent_and_reassesses_before_completion(self):
        reserved = self.view()
        completed = self.view(
            channel_active=False, status="completed", completed_at=timezone.now()
        )
        self.repository.reserve.return_value = ReservedDeactivation(reserved)
        self.lifecycle.assess_disable.side_effect = (
            DisableAssessment(
                "unlink_required",
                assessment_proof="a" * 64,
                target_resource_id=uuid4(),
            ),
            DisableAssessment("clear_to_disable"),
        )
        self.lifecycle.start_disable_unlink.return_value = SimpleNamespace(
            status="succeeded",
            operation=SimpleNamespace(status="succeeded", operation_id=self.operation_id),
        )
        self.repository.complete_inactive.return_value = CompletedDeactivation(
            completed, completed.channel_revision
        )

        result = self.coordinator.start(self.owner, self.command())

        unlink_command = self.lifecycle.start_disable_unlink.call_args.args[0]
        self.assertEqual(unlink_command.deactivation_operation_id, self.operation_id)
        self.assertEqual(result.view.status, "completed")

    # 2.2 RED: external/unknown/cleanupはactiveを維持した確認待ちへ保存する。
    def test_start_blocker_stays_active_and_saves_closed_reason(self):
        reserved = self.view()
        blocked = self.view(
            status="confirmation_required", safe_reason="external_default"
        )
        self.repository.reserve.return_value = ReservedDeactivation(reserved)
        self.lifecycle.assess_disable.return_value = DisableAssessment(
            "external_default_blocked", reason="external_default"
        )
        self.repository.save_result.return_value = SavedDeactivation(blocked)

        result = self.coordinator.start(self.owner, self.command())

        self.assertTrue(result.view.channel_active)
        self.assertEqual(result.view.safe_reason, "external_default")
        self.lifecycle.start_disable_unlink.assert_not_called()
        self.repository.complete_inactive.assert_not_called()

    # 2.2/2.3 remediation RED: unlink未受付失敗は存在しないsubjectを保存しない。
    def test_unlink_rejection_saves_no_subject_and_reassesses_on_recheck(self):
        reserved = self.view()
        blocked = self.view(
            status="confirmation_required", safe_reason="stale_channel"
        )
        self.repository.reserve.return_value = ReservedDeactivation(reserved)
        self.lifecycle.assess_disable.return_value = DisableAssessment(
            "unlink_required",
            assessment_proof="a" * 64,
            target_resource_id=uuid4(),
        )
        self.lifecycle.start_disable_unlink.return_value = SimpleNamespace(
            status="failed", code=SimpleNamespace(value="stale_channel")
        )
        self.repository.save_result.return_value = SavedDeactivation(blocked)

        self.coordinator.start(self.owner, self.command())

        saved = self.repository.save_result.call_args.args[0]
        self.assertIsNone(saved.subject_rich_operation_id)
        self.assertEqual(saved.safe_reason, "stale_channel")

    # 2.3 RED: 明示recheckだけがrevisionを前進し、subject有無でrecovery commandを分ける。
    def test_explicit_recheck_advances_revision_and_recovers_saved_subject(self):
        subject_id = uuid4()
        recovery_id = uuid4()
        pending = self.view(
            status="confirmation_required",
            safe_reason="recheck_required",
            subject_rich_operation_id=subject_id,
        )
        advanced = self.view(
            expected_channel_revision=self.revision,
            latest_recovery_operation_id=recovery_id,
            status="confirmation_required",
            safe_reason="recheck_required",
            subject_rich_operation_id=subject_id,
        )
        self.repository.get_for_owner.return_value = pending
        self.repository.advance_recheck_revision.return_value = LockedDeactivation(advanced)
        self.lifecycle.recover_disable.return_value = DisableRecoveryResult(
            recovery_id, None, DisableAssessment("clear_to_disable")
        )
        completed = self.view(
            channel_active=False, status="completed", completed_at=timezone.now()
        )
        self.repository.complete_inactive.return_value = CompletedDeactivation(
            completed, completed.channel_revision
        )

        result = self.coordinator.recheck(
            self.owner,
            RecheckDeactivation(
                self.channel_id, self.operation_id, recovery_id, self.revision
            ),
        )

        recovery = self.lifecycle.recover_disable.call_args.args[0]
        self.assertEqual(recovery.subject_operation_id, subject_id)
        self.assertEqual(result.view.status, "completed")

    # 2.3 RED: 同じrecovery IDの保存済み結果は再送せず一件へ収束する。
    def test_recheck_replay_returns_saved_result_without_external_call(self):
        recovery_id = uuid4()
        replay = self.view(
            status="confirmation_required",
            safe_reason="external_default",
            latest_recovery_operation_id=recovery_id,
            recovery_result_ready=True,
        )
        self.repository.get_for_owner.return_value = replay

        result = self.coordinator.recheck(
            self.owner,
            RecheckDeactivation(
                self.channel_id, self.operation_id, recovery_id, self.revision
            ),
        )

        self.assertEqual(result.view, replay)
        self.repository.advance_recheck_revision.assert_not_called()
        self.lifecycle.recover_disable.assert_not_called()

    # 2.3 remediation: recovery ID予約後に中断しても同じIDで外部recoveryを再開する。
    def test_incomplete_recovery_reservation_retries_same_id(self):
        recovery_id = uuid4()
        subject_id = uuid4()
        pending = self.view(
            status="confirmation_required",
            safe_reason="recheck_required",
            subject_rich_operation_id=subject_id,
            latest_recovery_operation_id=recovery_id,
            recovery_result_ready=False,
        )
        self.repository.get_for_owner.return_value = pending
        self.repository.advance_recheck_revision.return_value = LockedDeactivation(pending)
        self.lifecycle.recover_disable.return_value = DisableRecoveryResult(
            recovery_id, None, DisableAssessment("clear_to_disable")
        )
        completed = self.view(
            channel_active=False, status="completed", completed_at=timezone.now()
        )
        self.repository.complete_inactive.return_value = CompletedDeactivation(
            completed, completed.channel_revision
        )

        result = self.coordinator.recheck(
            self.owner,
            RecheckDeactivation(
                self.channel_id, self.operation_id, recovery_id, self.revision
            ),
        )

        self.lifecycle.recover_disable.assert_called_once()
        self.assertEqual(result.view.status, "completed")

    # 2.5 RED: 再有効化はchannelだけを変更しrich menu portを呼ばない。
    def test_reactivate_changes_only_channel_and_requires_fresh_reads(self):
        self.foundation.update.return_value = SimpleNamespace(
            status="succeeded",
            channel=SimpleNamespace(
                public_id=self.channel_id, updated_at=timezone.now()
            ),
        )

        result = self.coordinator.reactivate(
            self.owner, ReactivateChannel(self.channel_id, self.revision)
        )

        self.assertTrue(result.refresh_required)
        forwarded = self.foundation.update.call_args.args[0]
        self.assertTrue(forwarded.is_active)
        self.assertEqual(forwarded.expected_updated_at, self.revision)
        self.lifecycle.assess_disable.assert_not_called()
        self.lifecycle.start_disable_unlink.assert_not_called()
