from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from django.db import connection
from django.test import TransactionTestCase

from lineaccounts.admin_authorization import (
    OwnerActiveProof,
    OwnerFenceFailed,
    OwnerOperationContext,
)
from linechannels.admin_types import (
    ConnectionRevisionUnchanged,
    ExactChannelSnapshotAvailable,
    RichMenuChannelSnapshot,
)
from linechannels.types import AccessToken
from linerichmenus.current_service import CurrentMenuService
from linerichmenus.current_types import CurrentMenuSucceeded
from linerichmenus.gateway import (
    DownloadedImage,
    GatewayRejected,
    ImageAbsent,
    ImageObservationUnknown,
    RichMenuDefaultNone,
    RichMenuDefaultPresent,
    RichMenuDefaultUnknown,
)
from linerichmenus.models import ManagedRichMenu, RichMenuOperation
from linerichmenus.repository import DjangoCurrentReadRepository, DjangoRichMenuRepository
from linerichmenus.services import DefaultMutationReadiness
from linerichmenus.types import SafeResultCode

from . import test_current_repository as fixtures

NOW = fixtures.NOW
NoDeactivation = fixtures.NoDeactivation


class CurrentServiceFixture(TransactionTestCase):
    configuration = fixtures.CurrentRepositoryTests.configuration
    operation = fixtures.CurrentRepositoryTests.operation
    make_resource = fixtures.CurrentRepositoryTests.make_resource
    """保存関係fixtureを共有し、service専用ケースだけを収集する。"""

    def setUp(self):
        fixtures.CurrentRepositoryTests.setUp(self)
        self.owner = OwnerOperationContext(
            self.scope.owner_identity_public_id, self.scope.owner_identity_public_id
        )
        self.fence = Mock()
        self.fence.lock_active.return_value = OwnerActiveProof(
            self.scope.owner_identity_public_id, self.scope.provider_id
        )
        self.metadata = SimpleNamespace(
            public_id=self.scope.channel_public_id,
            provider_id=self.scope.provider_id,
            is_active=True,
            updated_at=NOW,
        )
        self.channel = Mock()
        self.channel.get_for_owner_provider.side_effect = lambda *args: self.metadata
        self.channel.lock_connection_revision.return_value = ConnectionRevisionUnchanged()
        self.channel.snapshot_exact.return_value = ExactChannelSnapshotAvailable(
            RichMenuChannelSnapshot(
                owner_identity_public_id=self.scope.owner_identity_public_id,
                provider_id=self.scope.provider_id,
                channel_public_id=self.scope.channel_public_id,
                channel_label="テスト",
                is_active=True,
                channel_revision=NOW,
                access_token=AccessToken("test-access-token"),
            )
        )
        self.gateway = Mock()
        self.gateway.get_default.side_effect = self.default
        self.gateway.download_content.side_effect = self.image
        self.image_result = DownloadedImage("image/png", 800, 400, "b" * 64, 3, binary=b"png")
        self.after_image = lambda: None
        self.recording = DjangoRichMenuRepository()
        self.service = CurrentMenuService(
            owner_fence=self.fence,
            channel_port=self.channel,
            current_repository=DjangoCurrentReadRepository(deactivation=NoDeactivation()),
            repository=self.recording,
            gateway=self.gateway,
            readiness=DefaultMutationReadiness(mode="enabled", integration_complete=True),
            clock=lambda: NOW,
        )

    def default(self, context):
        self.assertFalse(connection.in_atomic_block)
        return RichMenuDefaultPresent(self.resource.line_rich_menu_id)

    def image(self, context, rich_menu_id):
        self.assertFalse(connection.in_atomic_block)
        self.assertEqual(rich_menu_id, self.resource.line_rich_menu_id)
        self.after_image()
        return self.image_result

    def read(self):
        return self.service.get_current(self.owner, self.scope.channel_public_id, NOW)


class CurrentServiceObservationTests(CurrentServiceFixture):
    # テストケース: 管理対象の画像を取得し、既定を再照会する。
    # 期待値: 元bytes・対応設定・観測日時を返し、既定2回と画像1回だけ呼ぶ。
    def test_managed_original_bytes_and_call_budget(self):
        result = self.read()
        self.assertIsInstance(result, CurrentMenuSucceeded)
        self.assertEqual(result.current.kind, "managed")
        self.assertEqual(result.current.image.binary, b"png")
        self.assertEqual(result.current.configuration.status, "available")
        self.assertEqual(result.checked_at, NOW)
        self.assertEqual(self.gateway.get_default.call_count, 2)
        self.assertEqual(self.gateway.download_content.call_count, 1)
        self.assertEqual(
            {call[0] for call in self.gateway.mock_calls}, {"get_default", "download_content"}
        )

    # テストケース: 画像だけが欠落・不正・取得不能・例外になる。
    # 期待値: managedと設定を保持し、失敗時も既定を再照会する。
    def test_partial_image_failure(self):
        for image, reason in (
            (ImageAbsent(), "missing"),
            (GatewayRejected("image_invalid"), "invalid"),
            (ImageObservationUnknown(), "fetch_failed"),
            (ImageObservationUnknown("rate_limited"), "rate_limited"),
            (RuntimeError("private"), "fetch_failed"),
        ):
            with self.subTest(reason=reason):
                self.gateway.reset_mock()
                self.gateway.download_content.side_effect = (
                    image if isinstance(image, Exception) else lambda *args: image
                )
                result = self.read()
                self.assertEqual(result.current.image.reason, reason)
                self.assertEqual(result.current.kind, "managed")
                self.assertEqual(self.gateway.get_default.call_count, 2)

    # テストケース: 画像取得中に既定が消滅・変更・取得不能になる。
    # 期待値: 部分失敗へ縮退せず全体競合または観測失敗を返す。
    def test_second_default_failure(self):
        for second, code in (
            (RichMenuDefaultNone(), SafeResultCode.CURRENT_CHANGED),
            (RichMenuDefaultPresent("external"), SafeResultCode.CURRENT_CHANGED),
            (RichMenuDefaultUnknown(), SafeResultCode.OBSERVATION_UNKNOWN),
        ):
            self.gateway.get_default.side_effect = [
                RichMenuDefaultPresent(self.resource.line_rich_menu_id),
                second,
            ]
            result = self.read()
            self.assertEqual(result.code, code)

    # テストケース: 取得画像のdigestが保存値と異なる。
    # 期待値: 既定を再確認したうえで全体競合にする。
    def test_image_digest_conflict(self):
        self.image_result = replace(self.image_result, pixel_digest="a" * 64)
        self.assertEqual(self.read().code, SafeResultCode.CURRENT_CHANGED)
        self.assertEqual(self.gateway.get_default.call_count, 2)

    # テストケース: currentなしで未設定・外部既定、またはcurrentとの不一致を観測する。
    # 期待値: 正しい分類を返し、外部内容・画像を取得しない。
    def test_unset_external_attention(self):
        for current, observed, kind in (
            (False, RichMenuDefaultNone(), "unset"),
            (False, RichMenuDefaultPresent("external"), "external"),
            (True, RichMenuDefaultNone(), "attention"),
            (True, RichMenuDefaultPresent("external"), "attention"),
        ):
            self.state.current_resource = self.resource if current else None
            self.state.save()
            self.gateway.get_default.side_effect = lambda *args: observed
            result = self.read()
            self.assertEqual(result.current.kind, kind)
            self.assertIsNone(result.current.image)
        self.gateway.download_content.assert_not_called()

    # テストケース: 停止中のチャネルを読む。
    # 期待値: 資格情報を取得せず、外部照会せず、checkedAtなしの保存情報を返す。
    def test_inactive_without_credentials(self):
        self.metadata.is_active = False
        result = self.read()
        self.assertEqual(result.current.kind, "inactive")
        self.assertIsNone(result.checked_at)
        self.assertEqual(result.rich.current_resource.public_id, self.resource.pk)
        self.channel.snapshot_exact.assert_not_called()
        self.gateway.get_default.assert_not_called()

    # テストケース: owner・provider・revisionの初期認可に失敗する。
    # 期待値: 全体失敗となり資格情報やLINEへ到達しない。
    def test_initial_authorization(self):
        for change, code in (
            ({"provider_id": "other"}, SafeResultCode.CHANNEL_UNAVAILABLE),
            ({"updated_at": NOW + timedelta(seconds=1)}, SafeResultCode.STALE_CHANNEL),
        ):
            original = self.metadata
            self.metadata = SimpleNamespace(**(vars(original) | change))
            self.assertEqual(self.read().code, code)
            self.metadata = original
        self.fence.lock_active.return_value = OwnerFenceFailed("authentication_required")
        self.assertEqual(self.read().code, SafeResultCode.AUTHENTICATION_REQUIRED)
        self.gateway.get_default.assert_not_called()

    # テストケース: 画像取得中にowner認可または資源・操作・設定・停止状態が変わる。
    # 期待値: 最終fenceで拒否し観測を書き込まない。
    def test_final_fence(self):
        self.recording.record_observation = Mock(wraps=self.recording.record_observation)
        self.after_image = lambda: ManagedRichMenu.objects.filter(pk=self.resource.pk).update(
            image_digest="a" * 64
        )
        self.assertEqual(self.read().code, SafeResultCode.CURRENT_CHANGED)
        self.recording.record_observation.assert_not_called()
        ManagedRichMenu.objects.filter(pk=self.resource.pk).update(image_digest="b" * 64)
        self.after_image = lambda: None
        self.fence.lock_active.side_effect = [
            OwnerActiveProof(self.scope.owner_identity_public_id, self.scope.provider_id),
            OwnerFenceFailed("authentication_required"),
        ]
        self.assertEqual(self.read().code, SafeResultCode.AUTHENTICATION_REQUIRED)
        self.recording.record_observation.assert_not_called()

    # テストケース: 処理中・結果不明・確定済みcleanup待ちの現在資源を読む。
    # 期待値: cleanup待ちだけ画像を返し、他はattentionで画像と設定を隠す。
    def test_operation_restrictions_on_content(self):
        for status, active, kind in (
            ("processing", True, "attention"),
            ("unknown", False, "attention"),
            ("cleanup_required", False, "managed"),
        ):
            RichMenuOperation.objects.filter(pk=self.origin.pk).update(status=status)
            self.state.active_operation = self.origin if active else None
            self.state.blocking_operation = None if active else self.origin
            self.state.save()
            result = self.read()
            self.assertEqual(result.current.kind, kind)
            if kind == "attention":
                self.assertIsNone(result.current.configuration)


class CurrentServiceActionTests(CurrentServiceFixture):
    # テストケース: 観測とcurrentの有無に候補表を適用する。
    # 期待値: 適格なcurrentだけ管理終了を補い、外部資源や不正関係を対象にしない。
    def test_candidate_table(self):
        from linerichmenus.types import NextAllowedAction as A

        for current, observed, expected in (
            (
                True,
                RichMenuDefaultPresent(self.resource.line_rich_menu_id),
                {A.NEW_PREVIEW, A.APPLY, A.UNLINK, A.RELEASE},
            ),
            (True, RichMenuDefaultNone(), {A.NEW_PREVIEW, A.APPLY, A.RELEASE}),
            (True, RichMenuDefaultPresent("external"), {A.NEW_PREVIEW, A.APPLY, A.RELEASE}),
            (False, RichMenuDefaultNone(), {A.NEW_PREVIEW, A.APPLY}),
            (False, RichMenuDefaultPresent("external"), {A.NEW_PREVIEW, A.APPLY}),
        ):
            self.state.current_resource = self.resource if current else None
            self.state.save()
            self.gateway.get_default.side_effect = lambda *args: observed
            result = self.read()
            self.assertEqual(
                set(result.rich.next_allowed_actions), expected | {A.GET_STATE, A.VIEW_HISTORY}
            )
            self.assertEqual(
                set(result.rich.capabilities.actions), set(result.rich.next_allowed_actions)
            )
        self.state.current_resource = self.resource
        self.state.save()
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(provider_id="other")
        self.assertNotIn(A.RELEASE, self.read().rich.next_allowed_actions)

    # テストケース: mode制限と画像・設定だけの失敗を組み合わせる。
    # 期待値: 候補は維持し、modeはその部分集合だけを有効にする。
    def test_modes_and_partial_failures(self):
        from linerichmenus.types import NextAllowedAction as A

        self.image_result = ImageAbsent()
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(configuration_snapshot={})
        for mode, expected in (
            ("read_only", {A.GET_STATE, A.VIEW_HISTORY}),
            ("recovery_only", {A.UNLINK, A.RELEASE, A.GET_STATE, A.VIEW_HISTORY}),
            ("enabled", {A.NEW_PREVIEW, A.APPLY, A.UNLINK, A.RELEASE, A.GET_STATE, A.VIEW_HISTORY}),
        ):
            self.service._readiness = DefaultMutationReadiness(mode=mode, integration_complete=True)
            result = self.read()
            self.assertEqual(set(result.rich.capabilities.actions), expected)
            self.assertIn(A.RELEASE, result.rich.next_allowed_actions)

    # テストケース: active・unknown・cleanupと未解決旧資源を保持する。
    # 期待値: 通常変更を禁止し、有効なsubjectとtargetだけ回復候補にする。
    def test_operation_and_cleanup_restrictions(self):
        from linerichmenus.types import NextAllowedAction as A

        self.state.active_operation = self.origin
        self.state.save()
        self.assertEqual(set(self.read().rich.next_allowed_actions), {A.GET_STATE, A.VIEW_HISTORY})
        self.state.active_operation = None
        self.state.blocking_operation = self.origin
        self.state.save()
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="unknown")
        self.assertEqual(
            set(self.read().rich.next_allowed_actions), {A.RECHECK, A.GET_STATE, A.VIEW_HISTORY}
        )
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="cleanup_required")
        self.assertNotIn(A.CLEANUP, self.read().rich.next_allowed_actions)
        prior = self.operation()
        old = self.make_resource(prior, lifecycle="old", replacement_operation=self.origin)
        result = self.read()
        self.assertEqual(
            set(result.rich.next_allowed_actions), {A.CLEANUP, A.GET_STATE, A.VIEW_HISTORY}
        )
        self.assertEqual(result.recovery_targets[0].target_resource_id, old.pk)
        self.state.blocking_operation = None
        self.state.save()
        self.assertEqual(set(self.read().rich.next_allowed_actions), {A.GET_STATE, A.VIEW_HISTORY})

    # テストケース: 停止中とpending停止intentのあるチャネルを読む。
    # 期待値: 通常変更を提示せず、intentに許可されたcleanupだけ提示する。
    def test_inactive_and_pending(self):
        from linechannels.admin_lifecycle_types import DeactivationView
        from linerichmenus.types import NextAllowedAction as A

        self.metadata.is_active = False
        result = self.read()
        self.assertEqual(set(result.rich.next_allowed_actions), {A.GET_STATE, A.VIEW_HISTORY})
        self.assertFalse(result.recovery_targets)
        self.metadata.is_active = True
        port = Mock()
        intent = DeactivationView(
            self.scope.channel_public_id,
            True,
            NOW,
            self.owner.owner_session_id,
            NOW,
            "confirmation_required",
            None,
            self.origin.pk,
            None,
            NOW,
            NOW,
            None,
        )
        port.get_for_owner.return_value = intent
        self.service._current_repository = DjangoCurrentReadRepository(deactivation=port)
        self.assertEqual(set(self.read().rich.next_allowed_actions), {A.GET_STATE, A.VIEW_HISTORY})
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="cleanup_required")
        self.state.blocking_operation = self.origin
        self.state.save()
        prior = self.operation()
        self.make_resource(prior, lifecycle="old", replacement_operation=self.origin)
        port.get_for_owner.return_value = replace(intent, safe_reason="cleanup_required")
        result = self.read()
        self.assertIn(A.CLEANUP, result.rich.next_allowed_actions)
        self.assertNotIn(A.RELEASE, result.rich.next_allowed_actions)


class CurrentServiceFenceTests(CurrentServiceFixture):
    # テストケース: 外部通信中にrevision・provider・active・設定・操作・停止intentを変更する。
    # 期待値: 最終認可またはsnapshot比較で拒否し観測を保存しない。
    def test_changes_during_external_io(self):
        from linechannels.admin_lifecycle_types import DeactivationView

        recorder = Mock(wraps=self.recording.record_observation)
        self.recording.record_observation = recorder
        cases = (
            (
                lambda: setattr(self.metadata, "updated_at", NOW + timedelta(seconds=1)),
                SafeResultCode.STALE_CHANNEL,
            ),
            (
                lambda: setattr(self.metadata, "provider_id", "other"),
                SafeResultCode.CHANNEL_UNAVAILABLE,
            ),
            (lambda: setattr(self.metadata, "is_active", False), SafeResultCode.STALE_CHANNEL),
            (
                lambda: RichMenuOperation.objects.filter(pk=self.origin.pk).update(
                    configuration_snapshot={}
                ),
                SafeResultCode.CURRENT_CHANGED,
            ),
            (
                lambda: RichMenuOperation.objects.filter(pk=self.origin.pk).update(
                    status="unknown"
                ),
                SafeResultCode.CURRENT_CHANGED,
            ),
        )
        for mutation, code in cases:
            self.after_image = mutation
            result = self.read()
            self.assertEqual(result.code, code)
            recorder.assert_not_called()
            self.metadata.updated_at = NOW
            self.metadata.provider_id = self.scope.provider_id
            self.metadata.is_active = True
            RichMenuOperation.objects.filter(pk=self.origin.pk).update(
                status="succeeded", configuration_snapshot=self.configuration()
            )
        port = Mock()
        port.get_for_owner.return_value = None
        self.service._current_repository = DjangoCurrentReadRepository(deactivation=port)
        intent = DeactivationView(
            self.scope.channel_public_id,
            True,
            NOW,
            self.owner.owner_session_id,
            NOW,
            "checking",
            None,
            None,
            None,
            NOW,
            NOW,
            None,
        )
        self.after_image = lambda: setattr(port.get_for_owner, "return_value", intent)
        self.assertEqual(self.read().code, SafeResultCode.CURRENT_CHANGED)
        recorder.assert_not_called()

    # テストケース: 最初の照会がunknownまたは資格情報取得が拒否される。
    # 期待値: 安全な全体失敗だけを返し、画像を取得しない。
    def test_unknown_and_credentials_failure(self):
        from linechannels.admin_types import ExactChannelSnapshotRejected

        self.gateway.get_default.side_effect = lambda *args: RichMenuDefaultUnknown()
        self.assertEqual(self.read().code, SafeResultCode.OBSERVATION_UNKNOWN)
        self.gateway.download_content.assert_not_called()
        self.gateway.reset_mock()
        self.channel.snapshot_exact.return_value = ExactChannelSnapshotRejected(
            "credential_unreadable"
        )
        self.assertEqual(self.read().code, SafeResultCode.CHANNEL_UNAVAILABLE)
        self.gateway.get_default.assert_not_called()

    # テストケース: blocker参照のない結果不明なoriginがapplied資源に残る。
    # 期待値: 画像・設定・通常変更・管理終了を返さない。
    def test_unknown_origin_without_blocker(self):
        from linerichmenus.types import NextAllowedAction as A

        RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="unknown")
        result = self.read()
        self.assertEqual(result.current.kind, "attention")
        self.assertEqual(set(result.rich.next_allowed_actions), {A.GET_STATE, A.VIEW_HISTORY})
        self.gateway.download_content.assert_not_called()

    # テストケース: 現在資源の作成元をapply以外の確定済み操作にする。
    # 期待値: 不正な対応関係はattentionとし画像・設定・管理終了を返さない。
    def test_non_apply_origin_is_attention_without_image(self):
        from linerichmenus.types import NextAllowedAction as A

        RichMenuOperation.objects.filter(pk=self.origin.pk).update(
            kind="release", target_resource=self.resource
        )
        result = self.read()
        self.assertIsInstance(result, CurrentMenuSucceeded)
        self.assertEqual(result.current.kind, "attention")
        self.assertIsNone(result.current.configuration)
        self.assertNotIn(A.RELEASE, result.rich.next_allowed_actions)
        self.gateway.download_content.assert_not_called()

    # テストケース: 所有関係が有効なcurrentで設定欠損・不正・版廃止を読む。
    # 期待値: 設定の編集可否とは独立に現在画像と適格な管理終了を維持する。
    def test_configuration_failure_keeps_image_and_release(self):
        from linerichmenus.types import NextAllowedAction as A

        for configuration in (
            {},
            self.configuration() | {"fields": "invalid"},
            self.configuration() | {"templateVersion": 999},
        ):
            RichMenuOperation.objects.filter(pk=self.origin.pk).update(
                configuration_snapshot=configuration
            )
            result = self.read()
            self.assertEqual(result.current.kind, "managed")
            self.assertEqual(result.current.image.binary, b"png")
            self.assertIn(A.RELEASE, result.rich.capabilities.actions)

    # テストケース: 実owner/channel/停止adapterを含むmanaged閲覧を実行する。
    # 期待値: service全体のSQLが制御SQLを除き32回以内となる。
    def test_real_ports_query_budget(self):
        from uuid import uuid4

        from django.db import transaction
        from django.test.utils import CaptureQueriesContext

        from lineaccounts.admin_authorization import DjangoOwnerOperationFence
        from lineaccounts.gateway import VerifiedLineIdentity
        from lineaccounts.models import OwnerAccount
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
        with CaptureQueriesContext(connection) as queries:
            result = self.read()
        self.assertIsInstance(result, CurrentMenuSucceeded)
        sql = [
            query["sql"]
            for query in queries
            if not query["sql"].startswith(
                ("BEGIN", "COMMIT", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK")
            )
        ]
        self.assertLessEqual(len(sql), 32, "\n".join(sql))
