import threading
from datetime import timedelta
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from lineaccounts.admin_authorization import OwnerOperationContext
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import OwnerAccount
from lineaccounts.repositories import DjangoAccountRepository
from lineaccounts.types import LineSubject
from linechannels.admin_types import AdminServiceFailed, BotIdentityReceived, DeleteAdminChannel
from linechannels.container import build_channel_admin_service, build_line_channel_service
from linechannels.models import LineChannel
from linechannels.types import RegisterLineChannel, UpdateLineChannel
from linechannels.validators import build_credential_pair


class BlockingGateway:
    def __init__(self, bot_user_id):
        self.bot_user_id = bot_user_id
        self.started = threading.Event()
        self.release = threading.Event()
        self.in_transaction = None
        self.calls = 0

    def get_bot_identity(self, access_token):
        self.calls += 1
        self.in_transaction = transaction.get_connection().in_atomic_block
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("gateway release timed out")
        return BotIdentityReceived(self.bot_user_id)


class AdminConnectionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        OwnerAccount.objects.get_or_create(slot=1)
        repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = repository.lock_owner_account()
            identity = repository.upsert_identity(
                VerifiedLineIdentity(
                    provider_id="0012345678",
                    subject=LineSubject(f"U{uuid4().hex}"),
                    display_name="Owner",
                )
            )
            owner = repository.bind_owner_identity(owner, identity.public_id)
            session = repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )
        self.context = OwnerOperationContext(session.public_id, identity.public_id)
        self.bot_user_id = f"U{uuid4().hex}"
        result = build_line_channel_service().register(
            RegisterLineChannel(
                messaging_api_channel_id=str(uuid4().int)[:20],
                bot_user_id=self.bot_user_id,
                label="接続確認対象",
                credentials=build_credential_pair(
                    "connection-access-token-canary",
                    "connection-channel-secret-canary",
                ),
                is_active=False,
                provider_id="0012345678",
            )
        )
        self.assertEqual(result.status, "succeeded")
        self.channel_id = result.channel.public_id

    def run_check_during(self, mutation):
        gateway = BlockingGateway(self.bot_user_id)
        service = build_channel_admin_service()
        service._bot_info_gateway = gateway
        outcomes = []

        def check_connection():
            close_old_connections()
            try:
                outcomes.append(service.check_connection(self.context, self.channel_id))
            finally:
                close_old_connections()

        thread = threading.Thread(target=check_connection)
        thread.start()
        self.assertTrue(gateway.started.wait(5))
        self.assertFalse(gateway.in_transaction)
        mutation_result = mutation()
        gateway.release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(gateway.calls, 1)
        self.assertEqual(len(outcomes), 1)
        return outcomes[0], mutation_result

    # テストケース: 接続確認の外部call中に同じチャネルのmetadata revisionを更新する
    # 期待値: 外部call中はtransaction/row lockを保持せず、完了時に外部分類を破棄してstaleへ収束する
    def test_connection_check_releases_database_transaction_and_discards_stale_result(self):
        def update_metadata():
            before = LineChannel.objects.get(public_id=self.channel_id)
            return build_line_channel_service().update(
                UpdateLineChannel(
                    channel_public_id=self.channel_id,
                    label="接続確認中に更新",
                    expected_updated_at=before.updated_at,
                    required_provider_id="0012345678",
                )
            )

        outcome, updated = self.run_check_during(update_metadata)
        self.assertEqual(updated.status, "succeeded")
        self.assertEqual(outcome, AdminServiceFailed("stale_channel"))
        self.assertEqual(
            LineChannel.objects.get(public_id=self.channel_id).label,
            "接続確認中に更新",
        )

    # テストケース: 接続確認の外部call中に資格情報pairを置換する
    # 期待値: 新pairをcommitし、古いtokenで得た外部分類をstaleとして破棄する
    def test_connection_check_discards_result_after_concurrent_credential_replacement(self):
        def replace_credentials():
            before = LineChannel.objects.get(public_id=self.channel_id)
            return build_line_channel_service().update(
                UpdateLineChannel(
                    channel_public_id=self.channel_id,
                    credentials=build_credential_pair("new-token-canary", "new-secret-canary"),
                    expected_updated_at=before.updated_at,
                    required_provider_id="0012345678",
                )
            )

        outcome, updated = self.run_check_during(replace_credentials)
        self.assertEqual(updated.status, "succeeded")
        self.assertEqual(outcome, AdminServiceFailed("stale_channel"))

    # テストケース: 接続確認の外部call中に対象チャネルを削除する
    # 期待値: 削除を完了し、取得済みLINE分類をchannel_not_foundとして破棄する
    def test_connection_check_discards_result_after_concurrent_delete(self):
        def delete_channel():
            before = LineChannel.objects.get(public_id=self.channel_id)
            return build_channel_admin_service().delete(
                self.context,
                DeleteAdminChannel(self.channel_id, before.updated_at),
            )

        outcome, deleted = self.run_check_during(delete_channel)
        self.assertEqual(deleted.status, "succeeded")
        self.assertEqual(outcome, AdminServiceFailed("channel_not_found"))
        self.assertFalse(LineChannel.objects.filter(public_id=self.channel_id).exists())

    # テストケース: 接続確認の外部call中に同じowner sessionを失効させる
    # 期待値: 完了時のowner再検証が外部分類を破棄しauthentication_requiredへ収束する
    def test_connection_check_discards_result_after_concurrent_session_invalidation(self):
        def invalidate_session():
            with transaction.atomic():
                return DjangoAccountRepository().delete_owner_session(
                    self.context.owner_session_id
                )

        outcome, invalidated = self.run_check_during(invalidate_session)
        self.assertTrue(invalidated)
        self.assertEqual(outcome, AdminServiceFailed("authentication_required"))

    # テストケース: 接続確認の外部call中にowner unlink fenceを開始する
    # 期待値: 完了時のowner再検証が外部分類を破棄しowner_operation_blockedへ収束する
    def test_connection_check_discards_result_after_concurrent_unlink(self):
        def begin_unlink():
            with transaction.atomic():
                repository = DjangoAccountRepository()
                owner = repository.lock_owner_account()
                repository.begin_unlink(owner, uuid4())
            return "unlinking"

        outcome, state = self.run_check_during(begin_unlink)
        self.assertEqual(state, "unlinking")
        self.assertEqual(outcome, AdminServiceFailed("owner_operation_blocked"))
