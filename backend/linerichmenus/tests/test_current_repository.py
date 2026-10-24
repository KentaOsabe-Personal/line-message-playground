from datetime import UTC, datetime, timedelta
from uuid import uuid4

from django.db import transaction
from django.test import TestCase

from linerichmenus.current_types import ConfigurationAvailable, ConfigurationUnavailable
from linerichmenus.models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation
from linerichmenus.repository import DjangoCurrentReadRepository
from linerichmenus.types import OwnerChannelScope

NOW = datetime(2026, 10, 19, tzinfo=UTC)
URI = "https://example.com/path?full=value%20kept#anchor"


class NoDeactivation:
    def get_for_owner(self, owner_identity_public_id, provider_id, channel_public_id):
        return None


class CurrentRepositoryTests(TestCase):
    def setUp(self):
        self.scope = OwnerChannelScope(uuid4(), "0012345678", uuid4())
        self.state = RichMenuChannelState.objects.create(
            channel_public_id=self.scope.channel_public_id
        )
        self.origin = self.operation(configuration_snapshot=self.configuration())
        self.resource = self.make_resource(self.origin, lifecycle="applied")
        self.state.current_resource = self.resource
        self.state.save()
        self.repository = DjangoCurrentReadRepository(deactivation=NoDeactivation())

    def configuration(self):
        return {
            "templateId": "jp-link-one",
            "templateVersion": 1,
            "fields": [{"displayName": "公式サイト", "uri": URI}],
        }

    def operation(self, **changes):
        values = dict(
            operation_id=uuid4(),
            channel_state=self.state,
            owner_identity_public_id=self.scope.owner_identity_public_id,
            provider_id=self.scope.provider_id,
            kind="apply",
            status="succeeded",
            stage="verifying",
            request_fingerprint="a" * 64,
            expected_channel_revision=NOW,
            result_code="succeeded",
            accepted_at=NOW,
        )
        values.update(changes)
        return RichMenuOperation.objects.create(**values)

    def make_resource(self, origin, **changes):
        values = dict(
            channel_state=self.state,
            origin_operation=origin,
            ownership_marker="fixture:" + uuid4().hex,
            lifecycle="candidate",
            image_digest="b" * 64,
            line_rich_menu_id="richmenu-" + uuid4().hex,
        )
        values.update(changes)
        return ManagedRichMenu.objects.create(**values)

    def capture(self):
        with transaction.atomic():
            return self.repository.capture_current(self.scope)

    # テストケース: 現在資源の作成元より新しい履歴が存在し補助キーが欠けている。
    # 期待値: 現在資源の全設定と完全URLを復元し最新履歴とは混同しない。
    def test_current_origin_not_latest_history(self):
        latest = self.operation(accepted_at=NOW + timedelta(seconds=1))
        result = self.capture()
        self.assertIsInstance(result.configuration, ConfigurationAvailable)
        self.assertEqual(result.configuration.template.fields[0].uri, URI)
        self.assertEqual(result.configuration.editability, "editable")
        self.assertEqual(result.release_target_id, self.resource.pk)
        self.assertTrue(result.history_exists)
        self.assertEqual(result.latest_operation_id, latest.pk)
        self.assertNotIn(URI, repr(result))

    # テストケース: 作成元applyのowner・provider・channel・種別・確定状態をそれぞれ変える。
    # 期待値: 不正な対応から設定も管理終了対象も返さない。
    def test_invalid_origin_mapping(self):
        other_state = RichMenuChannelState.objects.create(channel_public_id=uuid4())
        for changes in (
            {"owner_identity_public_id": uuid4()},
            {"provider_id": "12345678"},
            {"channel_state": other_state},
            {"status": "unknown"},
            {"kind": "release", "target_resource": self.resource},
        ):
            with self.subTest(changes=tuple(changes)):
                RichMenuOperation.objects.filter(pk=self.origin.pk).update(**changes)
                result = self.capture()
                self.assertEqual(result.configuration, ConfigurationUnavailable("mapping_missing"))
                self.assertIsNone(result.release_target_id)
                RichMenuOperation.objects.filter(pk=self.origin.pk).update(
                    owner_identity_public_id=self.scope.owner_identity_public_id,
                    provider_id=self.scope.provider_id,
                    channel_state=self.state,
                    kind="apply",
                    status="succeeded",
                    target_resource=None,
                )

    # テストケース: 廃止版と欠落・不正な設定、正規化を必要とする保存値を読み込む。
    # 期待値: 廃止版だけ閲覧用に保持し不正値を修復せず編集不可へ分類する。
    def test_configuration_validation_and_retired_version(self):
        retired = self.configuration() | {"templateVersion": 999}
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(configuration_snapshot=retired)
        self.assertEqual(self.capture().configuration.editability, "template_unavailable")
        invalid = [
            None,
            {},
            self.configuration() | {"templateVersion": True},
            self.configuration() | {"templateId": ""},
            self.configuration() | {"fields": []},
            self.configuration() | {"fields": [{"displayName": " x ", "uri": URI}]},
            self.configuration()
            | {"fields": [{"displayName": "x", "uri": "https://u:p@example.com/"}]},
            self.configuration()
            | {"fields": [{"displayName": "x", "uri": "https://example.com:bad/"}]},
            self.configuration()
            | {"fields": [{"displayName": "x", "uri": "https://example.com/ x"}]},
            self.configuration() | {"fields": [{"displayName": "x", "uri": URI}] * 2},
        ]
        for value in invalid:
            with self.subTest(value=value):
                RichMenuOperation.objects.filter(pk=self.origin.pk).update(
                    configuration_snapshot=value
                )
                result = self.capture()
                self.assertEqual(
                    result.configuration, ConfigurationUnavailable("configuration_invalid")
                )
                self.assertEqual(result.release_target_id, self.resource.pk)

    # テストケース: 2領域の保存配列と確定済み後片付け待ちapplyを読む。
    # 期待値: descriptorの領域順に全項目を維持し適用確定状態を認める。
    def test_area_order_and_confirmed_cleanup(self):
        configuration = self.configuration() | {
            "templateId": "jp-link-two",
            "fields": [
                {"displayName": "左", "uri": URI},
                {"displayName": "右", "uri": "https://example.org/"},
            ],
        }
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(
            configuration_snapshot=configuration, status="cleanup_required"
        )
        result = self.capture()
        self.assertEqual(
            [field.display_name for field in result.configuration.template.fields], ["左", "右"]
        )
        self.assertEqual(result.release_target_id, self.resource.pk)

    # テストケース: blockerに関連する旧資源と関係不明・他ownerの資源が混在する。
    # 期待値: 既存受付と同じ関係を満たす対象組だけを返し履歴一覧を取得しない。
    def test_cleanup_targets_are_scoped_and_related(self):
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="cleanup_required")
        self.state.blocking_operation = self.origin
        self.state.save()
        prior = self.operation()
        related = self.make_resource(prior, lifecycle="old", replacement_operation=self.origin)
        self.make_resource(prior, lifecycle="old")
        foreign = self.operation(owner_identity_public_id=uuid4())
        self.make_resource(foreign, lifecycle="cleanup_required")
        result = self.capture()
        self.assertEqual(
            [
                (target.subject_operation_id, target.target_resource_id)
                for target in result.recovery_targets
            ],
            [(self.origin.pk, related.pk)],
        )
        self.state.active_operation = self.operation(status="accepted", stage=None)
        self.state.save()
        self.assertEqual(self.capture().recovery_targets, ())

    # テストケース: 同じ保存状態または観測日時だけが更新された状態を最終lockで比較する。
    # 期待値: 状態行のupdated_atを含む観測更新を競合にせずsnapshotを採用する。
    def test_fence_matches_and_ignores_observation(self):
        from linerichmenus.current_types import CurrentReadMatched

        expected = self.capture()
        self.assertIsInstance(
            self.repository.lock_current_unchanged(self.scope, expected), CurrentReadMatched
        )
        RichMenuChannelState.objects.filter(pk=self.state.pk).update(
            last_observation_kind="default_none",
            last_observation_fingerprint="c" * 64,
            last_observed_at=NOW,
            updated_at=NOW + timedelta(seconds=1),
        )
        self.assertIsInstance(
            self.repository.lock_current_unchanged(self.scope, expected), CurrentReadMatched
        )

    # テストケース: 取得後に現在参照・所有権・資源・作成元設定・操作の各比較軸を変更する。
    # 期待値: updated_atを更新しない差分も含め全てcurrent_changedへ収束する。
    def test_fence_detects_every_persisted_axis(self):
        from linerichmenus.current_types import CurrentReadConflict

        changes = [
            (RichMenuChannelState, self.state.pk, {"current_resource": None}),
            (RichMenuChannelState, self.state.pk, {"active_operation": self.origin}),
            (RichMenuChannelState, self.state.pk, {"blocking_operation": self.origin}),
            (ManagedRichMenu, self.resource.pk, {"image_digest": "c" * 64}),
            (ManagedRichMenu, self.resource.pk, {"line_rich_menu_id": "richmenu-changed"}),
            (ManagedRichMenu, self.resource.pk, {"lifecycle": "candidate"}),
            (ManagedRichMenu, self.resource.pk, {"ownership_marker": "changed-marker"}),
            (ManagedRichMenu, self.resource.pk, {"updated_at": NOW}),
            (RichMenuOperation, self.origin.pk, {"owner_identity_public_id": uuid4()}),
            (RichMenuOperation, self.origin.pk, {"provider_id": "other-provider"}),
            (RichMenuOperation, self.origin.pk, {"status": "unknown"}),
            (RichMenuOperation, self.origin.pk, {"stage": "cleaning"}),
            (RichMenuOperation, self.origin.pk, {"updated_at": NOW}),
            (
                RichMenuOperation,
                self.origin.pk,
                {
                    "configuration_snapshot": self.configuration()
                    | {"fields": [{"displayName": "変更", "uri": URI}]}
                },
            ),
        ]
        for model, pk, update in changes:
            with self.subTest(model=model.__name__, fields=tuple(update)):
                expected = self.capture()
                original = model.objects.filter(pk=pk).values(*update).get()
                model.objects.filter(pk=pk).update(**update)
                self.assertIsInstance(
                    self.repository.lock_current_unchanged(self.scope, expected),
                    CurrentReadConflict,
                )
                model.objects.filter(pk=pk).update(**original)

    # テストケース: 存在しなかったstate・資源・操作を作成し、取得済みの各行を消す。
    # 期待値: 空参照のstateと不存在を区別し新規作成・消滅を検出する。
    def test_fence_detects_creation_and_deletion(self):
        from linerichmenus.current_types import CurrentReadConflict

        empty_scope = OwnerChannelScope(
            self.scope.owner_identity_public_id, self.scope.provider_id, uuid4()
        )
        expected = self.repository.capture_current(empty_scope)
        empty = RichMenuChannelState.objects.create(channel_public_id=empty_scope.channel_public_id)
        self.assertIsInstance(
            self.repository.lock_current_unchanged(empty_scope, expected), CurrentReadConflict
        )
        expected = self.repository.capture_current(empty_scope)
        empty.delete()
        self.assertIsInstance(
            self.repository.lock_current_unchanged(empty_scope, expected), CurrentReadConflict
        )
        expected = self.capture()
        extra = self.make_resource(self.origin)
        self.assertIsInstance(
            self.repository.lock_current_unchanged(self.scope, expected), CurrentReadConflict
        )
        expected = self.capture()
        extra.delete()
        self.assertIsInstance(
            self.repository.lock_current_unchanged(self.scope, expected), CurrentReadConflict
        )
        expected = self.capture()
        extra_operation = self.operation(accepted_at=NOW + timedelta(days=1))
        self.assertIsInstance(
            self.repository.lock_current_unchanged(self.scope, expected), CurrentReadConflict
        )
        expected = self.capture()
        extra_operation.delete()
        self.assertIsInstance(
            self.repository.lock_current_unchanged(self.scope, expected), CurrentReadConflict
        )

    # テストケース: 停止intentの作成・status・更新時刻・消滅とportの取得失敗を返す。
    # 期待値: 停止前提の変化を競合にし取得失敗を不存在として採用しない。
    def test_deactivation_changes_and_port_failure(self):
        from dataclasses import replace

        from linechannels.admin_lifecycle_types import DeactivationConflict, DeactivationView
        from linerichmenus.current_types import CurrentReadConflict
        from linerichmenus.repository import CurrentReadUnavailable

        class MutableDeactivation:
            value = None

            def get_for_owner(self, *args):
                return self.value

        port = MutableDeactivation()
        self.repository = DjangoCurrentReadRepository(deactivation=port)
        expected = self.capture()
        intent = DeactivationView(
            self.scope.channel_public_id,
            True,
            NOW,
            uuid4(),
            NOW,
            "checking",
            None,
            None,
            None,
            NOW,
            NOW,
            None,
        )
        for changed in (
            intent,
            replace(intent, status="confirmation_required"),
            replace(intent, updated_at=NOW + timedelta(seconds=1)),
            None,
        ):
            port.value = changed
            self.assertIsInstance(
                self.repository.lock_current_unchanged(self.scope, expected), CurrentReadConflict
            )
            expected = self.capture()
        port.value = DeactivationConflict("storage_unavailable")
        with self.assertRaises(CurrentReadUnavailable):
            self.repository.lock_current_unchanged(self.scope, expected)

    # テストケース: 後片付け資源の置換関係を取得後に外す。
    # 期待値: cleanup対象の組が消える変更を検出する。
    def test_fence_detects_cleanup_relation_change(self):
        from linerichmenus.current_types import CurrentReadConflict

        RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="cleanup_required")
        self.state.blocking_operation = self.origin
        self.state.save()
        prior = self.operation()
        old = self.make_resource(prior, lifecycle="old", replacement_operation=self.origin)
        expected = self.capture()
        ManagedRichMenu.objects.filter(pk=old.pk).update(replacement_operation=None)
        self.assertIsInstance(
            self.repository.lock_current_unchanged(self.scope, expected), CurrentReadConflict
        )

    # テストケース: 停止portと自appの行lockの実行順序および閲覧による書込みを観測する。
    # 期待値: 停止portを先に読みstate→resource→operationの順でlockしINSERT/UPDATEしない。
    def test_lock_order_and_no_persistence(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        events = []

        class RecordingPort:
            def get_for_owner(self, *args):
                events.append("deactivation")
                return None

        def record(execute, sql, params, many, context):
            if "FOR UPDATE" in sql:
                for table in ("channelstate", "resource", "operation"):
                    if f"FROM `linerichmenus_{table}`" in sql:
                        events.append(table)
            return execute(sql, params, many, context)

        self.repository = DjangoCurrentReadRepository(deactivation=RecordingPort())
        with CaptureQueriesContext(connection) as queries, connection.execute_wrapper(record):
            expected = self.capture()
            self.repository.lock_current_unchanged(self.scope, expected)
        self.assertEqual(events, ["deactivation", "channelstate", "resource", "operation"] * 2)
        self.assertFalse(
            any(
                query["sql"].lstrip().startswith(("INSERT", "UPDATE", "DELETE"))
                for query in queries
            )
        )

    # テストケース: 失敗候補のcleanupと無関係な旧資源を同時に保持する。
    # 期待値: 作成元subjectに直接結び付く候補だけを回復対象とし旧資源を推測しない。
    def test_failed_candidate_cleanup_uses_direct_origin(self):
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="cleanup_required")
        ManagedRichMenu.objects.filter(pk=self.resource.pk).update(lifecycle="candidate")
        self.state.current_resource = None
        self.state.blocking_operation = self.origin
        self.state.save()
        self.make_resource(self.origin, lifecycle="old")
        targets = self.capture().recovery_targets
        self.assertEqual([target.target_resource_id for target in targets], [self.resource.pk])
