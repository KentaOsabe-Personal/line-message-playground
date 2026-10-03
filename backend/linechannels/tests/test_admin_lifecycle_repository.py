from datetime import timedelta
from uuid import uuid4

from django.test import TransactionTestCase

from linechannels.admin_lifecycle_repositories import (
    DjangoChannelDeactivationRepository,
    DjangoPendingDeactivationFence,
)
from linechannels.admin_lifecycle_types import (
    AdvanceDeactivationRevision,
    CompleteDeactivation,
    DeactivationConflict,
    RecordDeactivationRevisionConflict,
    ReservedDeactivation,
    ReserveDeactivation,
    SaveDeactivationResult,
)
from linechannels.models import ChannelDeactivationState, LineChannel


class ChannelDeactivationRepositoryTests(TransactionTestCase):
    def setUp(self):
        self.repository = DjangoChannelDeactivationRepository()
        self.owner_id = uuid4()
        self.provider_id = "0012345678"
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id="1234567890",
            bot_user_id="U" + uuid4().hex,
            label="対象",
            provider_id=self.provider_id,
            is_active=True,
        )

    def reserve(self, operation_id=None, revision=None):
        return self.repository.reserve(
            ReserveDeactivation(
                owner_identity_public_id=self.owner_id,
                provider_id=self.provider_id,
                channel_public_id=self.channel.public_id,
                operation_id=operation_id or uuid4(),
                expected_channel_revision=revision or self.channel.updated_at,
            )
        )

    # テストケース: 同じoperation IDと別operation IDで無効化intentを予約する。
    # 期待値: 同じIDはreplay、別IDは競合となり現在intentは一件だけ残る。
    def test_reserve_is_idempotent_and_rejects_competing_intent(self):
        operation_id = uuid4()
        first = self.reserve(operation_id)
        replay = self.reserve(operation_id)
        competing = self.reserve(uuid4())

        self.assertIsInstance(first, ReservedDeactivation)
        self.assertTrue(replay.replayed)
        self.assertIsInstance(competing, DeactivationConflict)
        self.assertEqual(ChannelDeactivationState.objects.count(), 1)

    # テストケース: channel更新後に古いrevisionの外部結果を保存し、明示recheckする。
    # 期待値: stale結果を拒否し、明示recheckだけが最新revisionへ前進する。
    def test_result_cas_conflict_and_explicit_revision_advance(self):
        reserved = self.reserve()
        old_revision = self.channel.updated_at
        self.channel.label = "並行更新"
        self.channel.save(update_fields=("label", "updated_at"))

        stale = self.repository.save_result(
            SaveDeactivationResult(
                owner_identity_public_id=self.owner_id,
                provider_id=self.provider_id,
                channel_public_id=self.channel.public_id,
                operation_id=reserved.view.operation_id,
                expected_channel_revision=old_revision,
                status="confirmation_required",
                safe_reason="external_default",
            )
        )
        self.assertIsInstance(stale, DeactivationConflict)

        recorded = self.repository.record_revision_conflict(
            RecordDeactivationRevisionConflict(
                owner_identity_public_id=self.owner_id,
                provider_id=self.provider_id,
                channel_public_id=self.channel.public_id,
                operation_id=reserved.view.operation_id,
            )
        )
        self.assertEqual(recorded.view.safe_reason, "stale_channel")
        self.assertEqual(recorded.view.expected_channel_revision, old_revision)

        advanced = self.repository.advance_recheck_revision(
            AdvanceDeactivationRevision(
                owner_identity_public_id=self.owner_id,
                provider_id=self.provider_id,
                channel_public_id=self.channel.public_id,
                operation_id=reserved.view.operation_id,
                recovery_operation_id=uuid4(),
                presented_channel_revision=self.channel.updated_at,
            )
        )
        self.assertEqual(advanced.view.expected_channel_revision, self.channel.updated_at)

    # テストケース: 予約済みintentを期待revisionで完了し、古いrevisionでも再実行する。
    # 期待値: channel無効化とintent完了を同時commitし、stale CASは部分更新せず拒否する。
    def test_complete_inactive_updates_channel_and_state_atomically(self):
        reserved = self.reserve()
        result = self.repository.complete_inactive(
            CompleteDeactivation(
                owner_identity_public_id=self.owner_id,
                provider_id=self.provider_id,
                channel_public_id=self.channel.public_id,
                operation_id=reserved.view.operation_id,
                expected_channel_revision=self.channel.updated_at,
            )
        )

        self.channel.refresh_from_db()
        state = ChannelDeactivationState.objects.get(line_channel=self.channel)
        self.assertFalse(self.channel.is_active)
        self.assertEqual(state.status, "completed")
        self.assertEqual(result.channel_revision, self.channel.updated_at)
        self.assertIsNotNone(state.completed_at)

        stale_revision = self.channel.updated_at - timedelta(microseconds=1)
        self.assertIsInstance(
            self.repository.complete_inactive(
                CompleteDeactivation(
                    owner_identity_public_id=self.owner_id,
                    provider_id=self.provider_id,
                    channel_public_id=self.channel.public_id,
                    operation_id=reserved.view.operation_id,
                    expected_channel_revision=stale_revision,
                )
            ),
            DeactivationConflict,
        )

    # テストケース: intent未作成チャネルを正しいproviderと別providerから取得する。
    # 期待値: 正しいscopeは空状態、別scopeは対象非開示のnot foundとして区別する。
    def test_get_distinguishes_empty_state_from_hidden_channel(self):
        self.assertIsNone(
            self.repository.get_for_owner(self.owner_id, self.provider_id, self.channel.public_id)
        )
        hidden = self.repository.get_for_owner(
            self.owner_id, "other-provider", self.channel.public_id
        )
        self.assertEqual(hidden.code, "channel_not_found")

    # テストケース: 完了済み無効化projectionのチャネルを再有効化して新intentを予約する。
    # 期待値: 過去資源を復元せず、一件の現在projectionを新operationへ置換する。
    def test_reactivated_channel_can_reserve_a_new_deactivation_intent(self):
        first = self.reserve()
        self.repository.complete_inactive(
            CompleteDeactivation(
                self.owner_id,
                self.provider_id,
                self.channel.public_id,
                first.view.operation_id,
                self.channel.updated_at,
            )
        )
        self.channel.refresh_from_db()
        self.channel.is_active = True
        self.channel.save(update_fields=("is_active", "updated_at"))
        second_id = uuid4()

        second = self.reserve(second_id, self.channel.updated_at)

        self.assertIsInstance(second, ReservedDeactivation)
        self.assertEqual(second.view.operation_id, second_id)
        self.assertEqual(second.view.status, "checking")
        self.assertEqual(ChannelDeactivationState.objects.count(), 1)

    # テストケース: deactivation modelのrecovery結果準備fieldを確認する。
    # 期待値: 予約直後と保存済み結果を区別できる安全なfalse既定値を持つ。
    def test_recovery_reservation_tracks_whether_result_is_persisted(self):
        field = ChannelDeactivationState._meta.get_field("recovery_result_ready")
        self.assertFalse(field.default)

    # テストケース: pending intent中に通常mutation、同じrecovery、対象cleanupを順にfenceする。
    # 期待値: 通常mutationを拒否し、保存intentに結び付く回復操作だけを許可する。
    def test_pending_deactivation_fence_allows_only_its_reserved_recovery(self):
        from types import SimpleNamespace

        from django.db import transaction

        self.reserve()
        fence = DjangoPendingDeactivationFence()
        ordinary = SimpleNamespace(
            channel_public_id=self.channel.public_id,
            owner_identity_public_id=self.owner_id,
            provider_id=self.provider_id,
            operation_id=uuid4(),
            subject_operation_id=None,
        )
        with transaction.atomic():
            self.assertFalse(fence.allows(ordinary))

        state = ChannelDeactivationState.objects.get(line_channel=self.channel)
        recovery_id = uuid4()
        subject_id = uuid4()
        state.status = "confirmation_required"
        state.safe_reason = "recheck_required"
        state.subject_rich_operation_id = subject_id
        state.latest_recovery_operation_id = recovery_id
        state.save(
            update_fields=(
                "status",
                "safe_reason",
                "subject_rich_operation_id",
                "latest_recovery_operation_id",
                "updated_at",
            )
        )
        recovery = SimpleNamespace(
            channel_public_id=self.channel.public_id,
            owner_identity_public_id=self.owner_id,
            provider_id=self.provider_id,
            operation_id=recovery_id,
            subject_operation_id=subject_id,
        )
        with transaction.atomic():
            self.assertTrue(fence.allows(recovery))

        state.safe_reason = "cleanup_required"
        state.subject_rich_operation_id = None
        state.latest_recovery_operation_id = None
        state.save(
            update_fields=(
                "safe_reason",
                "subject_rich_operation_id",
                "latest_recovery_operation_id",
                "updated_at",
            )
        )
        cleanup = SimpleNamespace(
            channel_public_id=self.channel.public_id,
            owner_identity_public_id=self.owner_id,
            provider_id=self.provider_id,
            operation_id=uuid4(),
            subject_operation_id=uuid4(),
            kind=SimpleNamespace(value="cleanup"),
        )
        with transaction.atomic():
            self.assertTrue(fence.allows(cleanup))

    # テストケース: pending intentを持つchannelの通常mutation lockを取得する。
    # 期待値: 同じlock区間でpendingを検出し、deactivation conflictとして拒否する。
    def test_channel_mutation_lock_rejects_pending_state(self):
        from django.db import transaction

        from linechannels.admin_repositories import DjangoAdminChannelRepository

        self.reserve()
        repository = DjangoAdminChannelRepository(cipher=None)
        with transaction.atomic():
            result = repository.lock_mutation_if_no_pending(
                self.channel.public_id,
                self.provider_id,
                self.channel.updated_at,
            )
        self.assertEqual(result, "deactivation_conflict")

    # テストケース: 別DB接続でchannel更新とdeactivation予約を同時実行する。
    # 期待値: 固定channel lock順で直列化し、両方を同時成功させない。
    def test_channel_mutation_and_deactivation_reserve_linearize_on_channel_lock(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from django.db import close_old_connections, transaction

        from linechannels.admin_repositories import DjangoAdminChannelRepository

        initial_revision = self.channel.updated_at
        mutation_locked = threading.Event()
        release_mutation = threading.Event()
        reserve_started = threading.Event()

        def mutate():
            close_old_connections()
            try:
                repository = DjangoAdminChannelRepository(cipher=None)
                with transaction.atomic():
                    result = repository.lock_mutation_if_no_pending(
                        self.channel.public_id,
                        self.provider_id,
                        initial_revision,
                    )
                    mutation_locked.set()
                    if not release_mutation.wait(5):
                        raise RuntimeError("mutation release timed out")
                    channel = LineChannel.objects.select_for_update().get(
                        public_id=self.channel.public_id
                    )
                    channel.label = "直列化更新"
                    channel.save(update_fields=("label", "updated_at"))
                return result
            finally:
                close_old_connections()

        def reserve():
            close_old_connections()
            try:
                reserve_started.set()
                return DjangoChannelDeactivationRepository().reserve(
                    ReserveDeactivation(
                        self.owner_id,
                        self.provider_id,
                        self.channel.public_id,
                        uuid4(),
                        initial_revision,
                    )
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            mutation = executor.submit(mutate)
            self.assertTrue(mutation_locked.wait(5))
            deactivation = executor.submit(reserve)
            self.assertTrue(reserve_started.wait(5))
            release_mutation.set()
            mutation_result = mutation.result(5)
            deactivation_result = deactivation.result(5)

        self.assertEqual(mutation_result, "allowed")
        self.assertIsInstance(deactivation_result, DeactivationConflict)
        self.assertEqual(deactivation_result.code, "stale_channel")
        self.assertFalse(ChannelDeactivationState.objects.exists())
