"""現在閲覧のMySQL並行transactionによる最終fence検証。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

from django.db import close_old_connections, connection, connections, transaction

from lineaccounts.admin_authorization import OwnerOperationContext
from lineaccounts.models import LineIdentity, OwnerAccount, OwnerSession
from linechannels.models import ChannelDeactivationState, LineChannel
from linechannels.types import AccessToken
from linerichmenus.models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation
from linerichmenus.repository import DjangoCurrentReadRepository
from linerichmenus.services import ServiceFailed
from linerichmenus.types import SafeResultCode

from .test_current_service import NOW, CurrentServiceFixture


class CurrentRealPortsFixture(CurrentServiceFixture):
    def setUp(self):
        super().setUp()
        if connection.vendor != "mysql":
            self.skipTest("MySQLの行lockと独立transactionが必要")
        from uuid import uuid4

        from lineaccounts.admin_authorization import DjangoOwnerOperationFence
        from lineaccounts.gateway import VerifiedLineIdentity
        from lineaccounts.repositories import DjangoAccountRepository
        from lineaccounts.types import LineSubject
        from linechannels import runtime
        from linechannels.admin_lifecycle_repositories import DjangoChannelDeactivationRepository
        from linechannels.admin_repositories import DjangoAdminChannelRepository
        from linechannels.crypto import FernetCredentialCipher
        from linechannels.models import LineChannel, LineChannelCredential
        from linechannels.types import CredentialContext

        accounts = DjangoAccountRepository()
        OwnerAccount.objects.get_or_create(slot=1)
        with transaction.atomic():
            owner = accounts.lock_owner_account()
            identity = accounts.upsert_identity(
                VerifiedLineIdentity(
                    self.scope.provider_id, LineSubject("U" + uuid4().hex), "Owner"
                )
            )
            owner = accounts.bind_owner_identity(owner, identity.public_id)
            session = accounts.create_owner_session(owner, NOW + timedelta(days=1))
        self.owner = OwnerOperationContext(session.public_id, identity.public_id)
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(
            owner_identity_public_id=identity.public_id
        )
        channels = DjangoAdminChannelRepository(
            FernetCredentialCipher(runtime.get_validated_keyring())
        )
        channel = LineChannel.objects.create(
            public_id=self.scope.channel_public_id,
            messaging_api_channel_id="1234567890",
            bot_user_id="U" + uuid4().hex,
            label="テスト",
            provider_id=self.scope.provider_id,
            is_active=True,
        )
        cipher = FernetCredentialCipher(runtime.get_validated_keyring())
        token = cipher.encrypt(
            AccessToken("test-token"), CredentialContext(channel.public_id, "access_token")
        )
        LineChannelCredential.objects.create(
            line_channel=channel,
            access_token_ciphertext=token.ciphertext,
            channel_secret_ciphertext=b"unused",
        )
        LineChannel.objects.filter(pk=channel.pk).update(updated_at=NOW)
        self.service._owner_fence = DjangoOwnerOperationFence(accounts)
        self.service._channel_port = channels
        self.service._current_repository = DjangoCurrentReadRepository(
            deactivation=DjangoChannelDeactivationRepository()
        )
        self.real_channel = channel
        self.session = session
        self.identity = identity


class CurrentMySQLConcurrencyTests(CurrentRealPortsFixture):
    def race(self, mutation, code, *, during="image"):
        entered, resume = Event(), Event()
        gateway_method = (
            self.gateway.download_content if during == "image" else self.gateway.get_default
        )
        original = self.image if during == "image" else self.default
        identities = []

        def blocked(*args):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(connection.get_autocommit())
            with connection.cursor() as cursor:
                cursor.execute("SELECT CONNECTION_ID()")
                identities.append(cursor.fetchone()[0])
            entered.set()
            if not resume.wait(10):
                raise AssertionError("download再開待ちtimeout")
            return original(*args)

        gateway_method.side_effect = blocked

        def read_separately():
            close_old_connections()
            try:
                return self.read()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(read_separately)
            try:
                self.assertTrue(entered.wait(10), "外部取得へ到達しない")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT CONNECTION_ID()")
                    self.assertNotEqual(cursor.fetchone()[0], identities[0])
                with transaction.atomic():
                    # 既存writerと同じowner → channel → rich stateの順にlockする。
                    OwnerAccount.objects.select_for_update().get(slot=1)
                    LineChannel.objects.select_for_update().get(pk=self.real_channel.pk)
                    list(RichMenuChannelState.objects.select_for_update().filter(pk=self.state.pk))
                    mutation()
            finally:
                resume.set()
            observed = result.result(timeout=10)
        self.assertIsInstance(observed, ServiceFailed)
        self.assertEqual(observed.code, code)
        self.assertFalse(hasattr(observed, "current"))
        self.assertNotIn("png", repr(observed))
        state = RichMenuChannelState.objects.filter(pk=self.state.pk).first()
        if state is not None:
            self.assertIsNone(state.last_observed_at)

    # テストケース: 画像download中に別transactionでsessionを削除する。
    # 期待値: lockを保持せず削除がcommitし、最終fenceが401相当で画像・設定を破棄する。
    def test_session_revoked_during_download(self):
        self.race(
            lambda: OwnerSession.objects.filter(public_id=self.session.public_id).delete(),
            SafeResultCode.AUTHENTICATION_REQUIRED,
        )

    # テストケース: 画像download中にownerを連携解除待ちへ変更する。
    # 期待値: 403相当の全体失敗となり観測・画像・設定を保存しない。
    def test_owner_disabled_during_download(self):
        self.race(
            lambda: OwnerAccount.objects.filter(slot=1).update(
                state="deauthorization_pending", unlink_generation=uuid4()
            ),
            SafeResultCode.OWNER_OPERATION_BLOCKED,
        )

    # テストケース: 画像download中にidentityのproviderを変更する。
    # 期待値: provider境界の404相当となり元providerの画像・設定を返さない。
    def test_provider_changed_during_download(self):
        self.race(
            lambda: LineIdentity.objects.filter(public_id=self.identity.public_id).update(
                provider_id="other-provider"
            ),
            SafeResultCode.CHANNEL_UNAVAILABLE,
        )

    # テストケース: 画像download中にchannel revisionを更新する。
    # 期待値: 409 stale_channelとして画像・設定を破棄する。
    def test_revision_changed_during_download(self):
        self.race(
            lambda: LineChannel.objects.filter(pk=self.real_channel.pk).update(
                updated_at=NOW + timedelta(seconds=1)
            ),
            SafeResultCode.STALE_CHANNEL,
        )

    # テストケース: 画像download中にcurrent参照を解除する。
    # 期待値: 409 current_changedとなり取得済み画像を部分成功として返さない。
    def test_current_removed_during_download(self):
        self.race(
            lambda: RichMenuChannelState.objects.filter(pk=self.state.pk).update(
                current_resource=None
            ),
            SafeResultCode.CURRENT_CHANGED,
        )

    # テストケース: 画像download中に作成元設定を別transactionで変更する。
    # 期待値: configuration digestの最終比較が変更を検出する。
    def test_origin_configuration_changed_during_download(self):
        self.race(
            lambda: RichMenuOperation.objects.filter(pk=self.origin.pk).update(
                configuration_snapshot={}
            ),
            SafeResultCode.CURRENT_CHANGED,
        )

    # テストケース: 画像download中に作成元operationをunknownへ変更する。
    # 期待値: 409 current_changedとなり旧設定・画像を公開しない。
    def test_operation_changed_during_download(self):
        self.race(
            lambda: RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="unknown"),
            SafeResultCode.CURRENT_CHANGED,
        )

    # テストケース: 停止intentのない初回取得後、download中にintentを新規作成する。
    # 期待値: 不存在から存在への変更を最終検証で拒否する。
    def test_deactivation_created_during_download(self):
        self.race(
            lambda: ChannelDeactivationState.objects.create(
                line_channel=self.real_channel,
                operation_id=uuid4(),
                owner_identity_public_id=self.identity.public_id,
                provider_id=self.scope.provider_id,
                expected_channel_revision=NOW,
                status="checking",
            ),
            SafeResultCode.CURRENT_CHANGED,
        )

    # テストケース: 停止intentを読んだ後、download中に同intentを削除する。
    # 期待値: 存在から不存在への変更を409で拒否する。
    def test_deactivation_deleted_during_download(self):
        intent = ChannelDeactivationState.objects.create(
            line_channel=self.real_channel,
            operation_id=uuid4(),
            owner_identity_public_id=self.identity.public_id,
            provider_id=self.scope.provider_id,
            expected_channel_revision=NOW,
            status="checking",
        )
        self.race(
            lambda: ChannelDeactivationState.objects.filter(pk=intent.pk).delete(),
            SafeResultCode.CURRENT_CHANGED,
        )

    # テストケース: download中にcurrent資源行を削除する。
    # 期待値: 外部取得中にlockで待たず削除でき、最終snapshotで変更を拒否する。
    def test_resource_deleted_during_download(self):
        self.race(
            lambda: ManagedRichMenu.objects.filter(pk=self.resource.pk).delete(),
            SafeResultCode.CURRENT_CHANGED,
        )

    # テストケース: state不存在で既定を取得中、別transactionからstateを作成する。
    # 期待値: 初回の不存在を固定せず、新規行を検出して409を返す。
    def test_state_created_during_observation(self):
        ManagedRichMenu.objects.filter(channel_state=self.state).delete()
        RichMenuChannelState.objects.filter(pk=self.state.pk).delete()
        self.race(
            lambda: RichMenuChannelState.objects.create(
                channel_public_id=self.scope.channel_public_id
            ),
            SafeResultCode.CURRENT_CHANGED,
            during="default",
        )

    # テストケース: download中に旧資源のreplacement関係を別transactionで変更する。
    # 期待値: cleanup対象の関係変更を検出し旧snapshotの操作可否を返さない。
    def test_cleanup_relation_changed_during_download(self):
        old = self.make_resource(self.origin, lifecycle="old", replacement_operation=self.origin)
        self.race(
            lambda: ManagedRichMenu.objects.filter(pk=old.pk).update(replacement_operation=None),
            SafeResultCode.CURRENT_CHANGED,
        )

    # テストケース: 初回・最終snapshot取得の行lock順をMySQLの発行SQLから観測する。
    # 期待値: 両transactionでowner・channel・state・resource・operationの既存順を維持する。
    def test_lock_order_in_both_snapshot_transactions(self):
        locked_tables = []

        def observe(execute, sql, params, many, context):
            if "FOR UPDATE" in sql:
                for table in (
                    "lineaccounts_owneraccount",
                    "linechannels_linechannel",
                    "linerichmenus_channelstate",
                    "linerichmenus_resource",
                    "linerichmenus_operation",
                ):
                    if f"FROM `{table}`" in sql:
                        locked_tables.append(table)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(observe):
            result = self.read()
        self.assertFalse(isinstance(result, ServiceFailed))
        for table in (
            "lineaccounts_owneraccount",
            "linechannels_linechannel",
            "linerichmenus_channelstate",
            "linerichmenus_resource",
            "linerichmenus_operation",
        ):
            self.assertGreaterEqual(locked_tables.count(table), 2)
        owner_positions = [
            i for i, table in enumerate(locked_tables) if table == "lineaccounts_owneraccount"
        ]
        for start in owner_positions:
            segment = locked_tables[start : start + 6]
            positions = [
                segment.index(table)
                for table in (
                    "lineaccounts_owneraccount",
                    "linechannels_linechannel",
                    "linerichmenus_channelstate",
                    "linerichmenus_resource",
                    "linerichmenus_operation",
                )
            ]
            self.assertEqual(positions, sorted(positions))
