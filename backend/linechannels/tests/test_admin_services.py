from unittest.mock import Mock
from uuid import uuid4

from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone

from lineaccounts.admin_authorization import (
    OwnerActiveProof,
    OwnerFenceFailed,
    OwnerOperationContext,
)
from linechannels.admin_services import DefaultChannelAdminService
from linechannels.admin_types import (
    AdminChannelView,
    AdminConnectionSnapshot,
    AdminRepositoryFailed,
    AdminRepositoryUnavailable,
    BotIdentityReceived,
    BotInfoFailed,
    ConnectionRevisionUnchanged,
    DeleteAdminChannel,
    RegisterAdminChannel,
    SetAdminChannelState,
    SnapshotAvailable,
    UpdateAdminChannel,
)
from linechannels.reference_fence import ReferenceCheckResult
from linechannels.types import (
    AccessToken,
    ChannelMutationFailed,
    ChannelMutationSucceeded,
    PublicChannelSummary,
)
from linechannels.validators import build_credential_pair


def channel_view(*, provider_id="000123", active=True):
    now = timezone.now()
    return AdminChannelView(
        public_id=uuid4(),
        messaging_api_channel_id="123456",
        bot_user_id="U" + uuid4().hex,
        label="管理対象",
        provider_id=provider_id,
        is_active=active,
        credentials_state="configured",
        credentials_updated_at=now,
        created_at=now,
        updated_at=now,
    )


class RecordingFence:
    def __init__(self, result=None):
        self.result = result or OwnerActiveProof(uuid4(), "000123")
        self.calls = []

    def lock_active(self, context, now):
        self.calls.append(
            (context, now, transaction.get_connection().in_atomic_block)
        )
        return self.result


class AdminChannelServiceTests(TransactionTestCase):
    def setUp(self):
        self.owner = OwnerOperationContext(uuid4(), uuid4())
        self.fence = RecordingFence()
        self.repository = Mock()
        self.foundation = Mock()
        self.references = Mock()
        self.gateway = Mock()
        self.clock = Mock(side_effect=lambda: timezone.now())
        self.service = DefaultChannelAdminService(
            self.fence,
            self.repository,
            self.foundation,
            self.references,
            self.gateway,
            clock=self.clock,
        )

    # テストケース: owner保護された一覧と詳細を同一providerで取得する
    # 期待値: owner fenceと投影を同一transactionで実行し、認証失敗時は投影しない
    def test_list_and_detail_are_owner_fenced_in_one_transaction(self):
        item = channel_view()
        self.repository.list_for_owner_provider.return_value = (item,)
        self.repository.get_for_owner_provider.return_value = item

        listed = self.service.list_channels(self.owner)
        detail = self.service.get_channel(self.owner, item.public_id)

        self.assertEqual(listed.channels, (item,))
        self.assertEqual(detail.channel, item)
        self.assertTrue(all(call[2] for call in self.fence.calls))
        self.repository.list_for_owner_provider.assert_called_once_with("000123")
        self.repository.get_for_owner_provider.assert_called_once_with(
            item.public_id, "000123"
        )

        blocked_repository = Mock()
        blocked = DefaultChannelAdminService(
            RecordingFence(OwnerFenceFailed("authentication_required")),
            blocked_repository,
            self.foundation,
            self.references,
            self.gateway,
        ).list_channels(self.owner)
        self.assertEqual(blocked.code, "authentication_required")
        blocked_repository.list_for_owner_provider.assert_not_called()

    # テストケース: ownerと異なるproviderおよび同一providerの登録を要求する
    # 期待値: 不一致はmutation前に拒否し、一致時だけ完全登録後のsafe投影を返す
    def test_register_rejects_provider_mismatch_before_mutation(self):
        credentials = build_credential_pair("access-token", "channel-secret")
        mismatched = RegisterAdminChannel(
            "123456", "U" + uuid4().hex, "対象", "999999", credentials, True
        )

        rejected = self.service.register(self.owner, mismatched)

        self.assertEqual(rejected.code, "provider_mismatch")
        self.foundation.register.assert_not_called()

        accepted = RegisterAdminChannel(
            "123456", "U" + uuid4().hex, "対象", "000123", credentials, True
        )
        view = channel_view()
        summary = PublicChannelSummary(
            view.public_id,
            view.messaging_api_channel_id,
            view.bot_user_id,
            view.label,
            view.is_active,
            True,
            view.created_at,
            view.updated_at,
            view.provider_id,
        )
        self.foundation.register.return_value = ChannelMutationSucceeded(summary)
        self.repository.get_for_owner_provider.return_value = view

        succeeded = self.service.register(self.owner, accepted)

        self.assertEqual(succeeded.channel, view)
        command = self.foundation.register.call_args.args[0]
        self.assertEqual(command.provider_id, "000123")
        self.assertNotIn("access-token", repr(command))

    # テストケース: metadata更新とwrite-only資格情報置換を要求する
    # 期待値: expected revisionとowner providerをfoundationへ渡し、safe投影だけを返す
    def test_update_forwards_revision_provider_and_credentials_safely(self):
        view = channel_view()
        credentials = build_credential_pair("replacement-token", "replacement-secret")
        command = UpdateAdminChannel(
            view.public_id,
            view.updated_at,
            label="更新後",
            credentials=credentials,
        )
        summary = PublicChannelSummary(
            view.public_id,
            view.messaging_api_channel_id,
            view.bot_user_id,
            "更新後",
            True,
            True,
            view.created_at,
            timezone.now(),
            "000123",
        )
        updated_view = channel_view()
        self.foundation.update.return_value = ChannelMutationSucceeded(summary)
        self.repository.get_for_owner_provider.return_value = updated_view

        result = self.service.update(self.owner, command)

        self.assertEqual(result.channel, updated_view)
        forwarded = self.foundation.update.call_args.args[0]
        self.assertEqual(forwarded.expected_updated_at, view.updated_at)
        self.assertEqual(forwarded.required_provider_id, "000123")
        self.assertIs(forwarded.credentials, credentials)
        self.assertNotIn("replacement-token", repr(result))

    # テストケース: 資格情報修復付きenableとcredential欠損enableを要求する
    # 期待値: 修復pairを状態変更と一体で渡し、欠損はsafe credential分類にする
    def test_set_state_integrates_optional_credential_repair(self):
        view = channel_view(active=False)
        credentials = build_credential_pair("repair-token", "repair-secret")
        command = SetAdminChannelState(
            view.public_id, view.updated_at, True, credentials
        )
        summary = PublicChannelSummary(
            view.public_id,
            view.messaging_api_channel_id,
            view.bot_user_id,
            view.label,
            True,
            True,
            view.created_at,
            timezone.now(),
            "000123",
        )
        self.foundation.update.return_value = ChannelMutationSucceeded(summary)
        self.repository.get_for_owner_provider.return_value = channel_view()

        result = self.service.set_state(self.owner, command)

        self.assertEqual(result.status, "succeeded")
        forwarded = self.foundation.update.call_args.args[0]
        self.assertTrue(forwarded.is_active)
        self.assertIs(forwarded.credentials, credentials)
        self.assertEqual(forwarded.required_provider_id, "000123")

        self.foundation.update.return_value = ChannelMutationFailed("invalid_transition")
        failed = self.service.set_state(self.owner, command)
        self.assertEqual(failed.code, "credential_unavailable")

    # テストケース: 参照中と未参照のチャネルを削除する
    # 期待値: channel lockとrevision確認後に参照を調べ、未参照時だけ原子削除する
    def test_delete_checks_references_after_channel_lock(self):
        view = channel_view(active=False)
        command = DeleteAdminChannel(view.public_id, view.updated_at)
        self.repository.lock_for_delete.return_value = view
        self.references.is_referenced.return_value = ReferenceCheckResult("referenced")

        referenced = self.service.delete(self.owner, command)

        self.assertEqual(referenced.code, "channel_referenced")
        self.repository.delete_locked.assert_not_called()

        self.references.is_referenced.return_value = ReferenceCheckResult("unreferenced")
        self.repository.delete_locked.return_value = (view.public_id, view.label)
        deleted = self.service.delete(self.owner, command)

        self.assertEqual(deleted.channel_public_id, view.public_id)
        self.assertEqual(deleted.label, view.label)
        self.repository.delete_locked.assert_called_once_with(view)

    # テストケース: snapshot取得から外部bot identity取得とrevision再検証まで実行する
    # 期待値: 外部call中はtransactionを保持せず、一致時だけconnectedを返す
    def test_connection_check_calls_gateway_outside_transaction_and_revalidates(self):
        channel_id = uuid4()
        expected_bot_id = "U" + uuid4().hex
        snapshot = AdminConnectionSnapshot(
            access_token=AccessToken("snapshot-token"),
            expected_bot_user_id=expected_bot_id,
            expected_updated_at=timezone.now(),
        )
        self.repository.get_connection_snapshot.return_value = SnapshotAvailable(snapshot)
        self.repository.lock_connection_revision.return_value = (
            ConnectionRevisionUnchanged()
        )

        def gateway_call(token):
            self.assertFalse(transaction.get_connection().in_atomic_block)
            self.assertNotIn("snapshot-token", repr(token))
            return BotIdentityReceived(expected_bot_id)

        self.gateway.get_bot_identity.side_effect = gateway_call

        result = self.service.check_connection(self.owner, channel_id)

        self.assertEqual(result.status, "connected")
        self.assertEqual(result.scope, "access_token_and_bot_identity_only")
        self.assertIsNotNone(result.checked_at.tzinfo)
        self.assertEqual(len(self.fence.calls), 2)
        self.repository.lock_connection_revision.assert_called_once_with(
            channel_id, "000123", snapshot.expected_updated_at
        )

    # テストケース: 外部結果後にchannel revisionが変化する
    # 期待値: LINE分類を破棄してstale_channelだけを返す
    def test_connection_check_discards_gateway_result_when_revision_is_stale(self):
        snapshot = AdminConnectionSnapshot(
            access_token=AccessToken("snapshot-token"),
            expected_bot_user_id="U" + uuid4().hex,
            expected_updated_at=timezone.now(),
        )
        self.repository.get_connection_snapshot.return_value = SnapshotAvailable(snapshot)
        self.gateway.get_bot_identity.return_value = BotInfoFailed("rate_limited")
        self.repository.lock_connection_revision.return_value = AdminRepositoryFailed(
            "stale_channel"
        )

        result = self.service.check_connection(self.owner, uuid4())

        self.assertEqual(result.code, "stale_channel")
        self.assertNotIn("rate_limited", repr(result))

    # テストケース: bot ID不一致とgatewayの3種safe失敗をrevision一致後に確定する
    # 期待値: identity mismatch、認証失敗、rate limit、LINE利用不能を限定scopeで返す
    def test_connection_check_maps_all_external_outcomes_after_revalidation(self):
        expected_bot_id = "U" + uuid4().hex
        snapshot = AdminConnectionSnapshot(
            access_token=AccessToken("snapshot-token"),
            expected_bot_user_id=expected_bot_id,
            expected_updated_at=timezone.now(),
        )
        self.repository.get_connection_snapshot.return_value = SnapshotAvailable(snapshot)
        self.repository.lock_connection_revision.return_value = (
            ConnectionRevisionUnchanged()
        )
        cases = (
            (BotIdentityReceived("U" + uuid4().hex), "identity_mismatch"),
            (BotInfoFailed("authentication_failed"), "authentication_failed"),
            (BotInfoFailed("rate_limited"), "rate_limited"),
            (BotInfoFailed("line_unavailable"), "line_unavailable"),
        )

        for gateway_result, expected in cases:
            with self.subTest(expected=expected):
                self.gateway.get_bot_identity.return_value = gateway_result
                result = self.service.check_connection(self.owner, uuid4())
                self.assertEqual(result.status, expected)
                self.assertEqual(
                    result.scope, "access_token_and_bot_identity_only"
                )

    # テストケース: 資格情報snapshotを安全に取得できない
    # 期待値: LINEを呼ばずcredential_unavailableの限定scope結果を返す
    def test_connection_check_does_not_call_line_for_unavailable_credentials(self):
        self.repository.get_connection_snapshot.return_value = (
            AdminRepositoryUnavailable("credential_unavailable")
        )

        result = self.service.check_connection(self.owner, uuid4())

        self.assertEqual(result.status, "credential_unavailable")
        self.gateway.get_bot_identity.assert_not_called()
