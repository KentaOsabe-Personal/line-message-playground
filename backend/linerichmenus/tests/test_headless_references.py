from datetime import UTC, datetime
from unittest.mock import call
from uuid import uuid4

from django.db import DatabaseError, transaction
from django.test import TransactionTestCase

from linechannels.models import LineChannel
from linechannels.reference_fence import ReferenceFenceResult
from linerichmenus.headless import (
    DjangoHeadlessReferenceContracts,
    DefaultRichMenuLifecyclePort,
    HeadlessCommand,
    HeadlessContractProgrammingError,
)
from linerichmenus.models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation
from linerichmenus.services import OperationSucceeded, ServiceFailed, StateSucceeded
from linerichmenus.types import (
    ChannelStateView,
    DefaultObservation,
    HistorySummary,
    ObservationKind,
    OperationCommand,
    OperationKind,
    OperationStatus,
    OperationView,
    SafeResultCode,
)
from lineaccounts.admin_authorization import OwnerOperationContext


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class FailingPurgeContracts(DjangoHeadlessReferenceContracts):
    def _delete_operations(self, state):
        super()._delete_operations(state)
        raise DatabaseError("injected after partial purge")


class HeadlessLifecyclePortTests(TransactionTestCase):
    # テストケース: default解除確認済みでblockerのないchannelをdisable前に照合する。
    # 期待値: guardだけがclear_to_disableを返し、同じserviceへowner contextを渡す。
    def test_guard_is_clear_only_after_confirmed_unlink_without_blockers(self):
        channel_id = uuid4()
        owner = OwnerOperationContext(uuid4(), uuid4())
        service = self._service()
        service.get_state.return_value = StateSucceeded(
            ChannelStateView(
                channel_public_id=channel_id,
                current_resource=None,
                blocking_operation=None,
                active_operation=None,
                cleanup_resources=(),
                latest_observation=DefaultObservation(
                    ObservationKind.DEFAULT_NONE, NOW, "a" * 64, None
                ),
                history_summary=HistorySummary(0, None, None),
                next_allowed_actions=(),
            )
        )

        result = DefaultRichMenuLifecyclePort(service).get_guard_state(
            HeadlessCommand(
                owner=owner,
                channel_public_id=channel_id,
                expected_channel_revision=NOW,
            )
        )

        self.assertEqual(result.status, "clear_to_disable")
        service.get_state.assert_called_once_with(
            owner, channel_id, expected_channel_revision=NOW
        )

    # テストケース: headless guardのchannel revisionがstaleと判定される。
    # 期待値: clear_to_disableへ進めずstale理由のunavailableへ縮約する。
    def test_guard_blocks_stale_channel_revision(self):
        channel_id = uuid4()
        owner = OwnerOperationContext(uuid4(), uuid4())
        service = self._service()
        service.get_state.return_value = ServiceFailed(SafeResultCode.STALE_CHANNEL)

        result = DefaultRichMenuLifecyclePort(service).get_guard_state(
            HeadlessCommand(owner, channel_id, NOW)
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "stale_channel")
        service.get_state.assert_called_once_with(
            owner, channel_id, expected_channel_revision=NOW
        )

    # テストケース: headless unlink/recheckを下流lifecycleから開始する。
    # 期待値: readinessを迂回せずowner APIと同じstart_operationへそのまま委譲する。
    def test_unlink_and_recheck_delegate_to_same_operation_service(self):
        channel_id = uuid4()
        owner = OwnerOperationContext(uuid4(), uuid4())
        service = self._service()
        unlink = OperationCommand(
            uuid4(), channel_id, NOW, OperationKind.UNLINK,
            None, uuid4(),
        )
        recheck = OperationCommand(
            uuid4(), channel_id, NOW, OperationKind.RECHECK,
            uuid4(), None,
        )
        operation = OperationView(
            unlink.operation_id, OperationKind.UNLINK, OperationStatus.SUCCEEDED,
            None, SafeResultCode.SUCCEEDED, None, unlink.target_resource_id,
            NOW, NOW, (),
        )
        service.start_operation.return_value = OperationSucceeded(operation)
        port = DefaultRichMenuLifecyclePort(service)

        self.assertIsInstance(
            port.start_unlink(HeadlessCommand(owner, channel_id, NOW, unlink)),
            OperationSucceeded,
        )
        self.assertIsInstance(
            port.recheck(HeadlessCommand(owner, channel_id, NOW, recheck)),
            OperationSucceeded,
        )
        self.assertEqual(
            service.start_operation.call_args_list,
            [call(owner, unlink), call(owner, recheck)],
        )

    @staticmethod
    def _service():
        from unittest.mock import Mock

        return Mock()


class HeadlessReferenceContractTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.channel = LineChannel.objects.create(
            public_id=uuid4(), messaging_api_channel_id="1234567890",
            bot_user_id="U" + uuid4().hex, label="削除対象",
            provider_id="0012345678", is_active=True,
        )
        self.state = RichMenuChannelState.objects.create(channel_public_id=self.channel.public_id)
        self.contracts = DjangoHeadlessReferenceContracts()

    # テストケース: lifecycle/statusごとの削除参照をprobeする。
    # 期待値: applied・processing・unknown・cleanup待ちだけが削除を阻止する。
    def test_probe_blocks_only_live_or_unresolved_state(self):
        terminal = self._operation(status="succeeded", stage="verifying")
        resource = ManagedRichMenu.objects.create(
            channel_state=self.state, origin_operation=terminal,
            ownership_marker="lrm:v1:" + uuid4().hex, lifecycle="deleted",
            image_digest="a" * 64, deleted_at=NOW,
        )
        self.assertFalse(self.contracts.is_referenced(self.channel.public_id))

        resource.lifecycle = "applied"
        resource.deleted_at = None
        resource.save(update_fields=("lifecycle", "deleted_at"))
        self.assertTrue(self.contracts.is_referenced(self.channel.public_id))
        resource.lifecycle = "deleted"
        resource.deleted_at = NOW
        resource.save(update_fields=("lifecycle", "deleted_at"))

        for status in ("processing", "unknown", "cleanup_required"):
            operation = self._operation(status=status, stage="cleaning")
            with self.subTest(status=status):
                self.assertTrue(self.contracts.is_referenced(self.channel.public_id))
            operation.delete()

    # テストケース: purgeをchannel削除transaction外から呼ぶ。
    # 期待値: programming errorとして拒否し履歴を変更しない。
    def test_purge_requires_caller_transaction(self):
        self._terminal_history()
        with self.assertRaises(HeadlessContractProgrammingError):
            self.contracts.purge_history(self.channel.public_id)
        self.assertTrue(RichMenuChannelState.objects.filter(pk=self.state.pk).exists())

    # テストケース: blockerがあるのに呼出側がpurge失敗結果を無視する。
    # 期待値: transaction全体がrollbackされchannel状態変更をcommitできない。
    def test_blocked_purge_marks_caller_transaction_rollback_only(self):
        self._operation(status="unknown", stage="creating")
        with transaction.atomic():
            self.channel.label = "誤って変更"
            self.channel.save(update_fields=("label",))
            result = self.contracts.purge_history(self.channel.public_id)
            self.assertEqual(result.status, "blocked")
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.label, "削除対象")
        self.assertTrue(RichMenuChannelState.objects.filter(pk=self.state.pk).exists())

    # テストケース: terminal history-only channelを同じ削除transactionでpurgeする。
    # 期待値: nullable recovery relationを安全に解除し4 rich-menu tableの対象行を削除できる。
    def test_terminal_history_can_be_purged_with_channel_deletion(self):
        subject, recovery = self._terminal_history(with_recovery=True)
        with transaction.atomic():
            result = self.contracts.purge_history(self.channel.public_id)
            self.assertEqual(result.status, "purged")
            self.channel.delete()
        self.assertFalse(RichMenuChannelState.objects.filter(pk=self.state.pk).exists())
        self.assertFalse(RichMenuOperation.objects.filter(pk__in=(subject.pk, recovery.pk)).exists())

    # テストケース: purge開始時のchannel reference fenceを記録する。
    # 期待値: channel state lockより前にaccept側と同じchannel行lockを取得する。
    def test_purge_locks_shared_channel_reference_fence(self):
        self._terminal_history()
        calls = []

        class RecordingFence:
            def lock_existing(inner_self, channel_public_id):
                calls.append(channel_public_id)
                return ReferenceFenceResult("locked")

        contracts = DjangoHeadlessReferenceContracts(reference_fence=RecordingFence())
        with transaction.atomic():
            result = contracts.purge_history(self.channel.public_id)
        self.assertEqual(result.status, "purged")
        self.assertEqual(calls, [self.channel.public_id])

    # テストケース: resource削除後のoperation purgeでstorage failureが起きる。
    # 期待値: 部分削除を残さずcaller transaction全体がrollbackされる。
    def test_partial_purge_failure_is_rollback_only(self):
        self._terminal_history()
        contracts = FailingPurgeContracts()
        with transaction.atomic():
            self.channel.label = "部分失敗"
            self.channel.save(update_fields=("label",))
            result = contracts.purge_history(self.channel.public_id)
            self.assertEqual(result.status, "storage_unavailable")
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.label, "削除対象")
        self.assertTrue(RichMenuChannelState.objects.filter(pk=self.state.pk).exists())
        self.assertTrue(ManagedRichMenu.objects.filter(channel_state=self.state).exists())

    def _terminal_history(self, with_recovery=False):
        subject = self._operation(status="succeeded", stage="verifying")
        ManagedRichMenu.objects.create(
            channel_state=self.state, origin_operation=subject,
            ownership_marker="lrm:v1:" + uuid4().hex, lifecycle="deleted",
            image_digest="b" * 64, deleted_at=NOW,
        )
        if not with_recovery:
            return subject
        recovery = self._operation(
            status="succeeded", stage="verifying", kind="recheck", subject=subject
        )
        return subject, recovery

    def _operation(self, *, status, stage, kind="apply", subject=None):
        return RichMenuOperation.objects.create(
            operation_id=uuid4(), channel_state=self.state,
            owner_identity_public_id=uuid4(), provider_id="0012345678", kind=kind,
            subject_operation=subject, request_fingerprint=uuid4().hex * 2,
            expected_channel_revision=NOW, status=status, stage=stage,
            result_code="succeeded" if status == "succeeded" else "response_unknown",
            accepted_at=NOW, completed_at=NOW if status == "succeeded" else None,
        )
