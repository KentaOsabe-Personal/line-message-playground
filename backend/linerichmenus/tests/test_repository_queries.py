from datetime import UTC, datetime, timedelta
from uuid import uuid4

from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from linechannels.models import LineChannel
from linerichmenus.models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation, RichMenuOperationTransition
from linerichmenus.repository import DjangoRichMenuRepository, HistoryQuery, OwnerChannelScope
from linerichmenus.types import OperationStatus


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class RichMenuRepositoryQueryTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.channel_id = uuid4()
        LineChannel.objects.create(
            public_id=self.channel_id,
            messaging_api_channel_id="1234567890",
            bot_user_id="U" + uuid4().hex,
            label="履歴用チャネル",
            provider_id="0012345678",
            is_active=True,
        )
        self.state = RichMenuChannelState.objects.create(
            channel_public_id=self.channel_id,
            last_observation_kind="default_none",
            last_observation_fingerprint="9" * 64,
            last_observed_at=NOW,
        )
        self.owner = uuid4()
        self.other_owner = uuid4()
        self.operations = [self._operation(index) for index in range(4)]
        self.other_operation = self._operation(9, owner=self.other_owner)
        resource = ManagedRichMenu.objects.create(
            channel_state=self.state,
            origin_operation=self.operations[0],
            ownership_marker="lrm:v1:" + uuid4().hex,
            lifecycle="applied",
            image_digest="8" * 64,
        )
        self.state.current_resource = resource
        self.state.save(update_fields=("current_resource",))
        self.repository = DjangoRichMenuRepository()

    # テストケース: owner scopeの保存済みchannel stateを取得する。
    # 期待値: current resource、観測、owner自身の履歴summaryとnext actionを一貫して返す。
    def test_get_state_builds_owner_scoped_projection(self):
        view = self.repository.get_state(self._scope())

        self.assertEqual(view.channel_public_id, self.channel_id)
        self.assertEqual(view.current_resource.lifecycle.value, "applied")
        self.assertEqual(view.latest_observation.fingerprint, "9" * 64)
        self.assertEqual(view.history_summary.total_count, 4)
        self.assertNotEqual(view.history_summary.latest_operation_id, self.other_operation.operation_id)

    # テストケース: limit付きowner履歴を複数page取得する。
    # 期待値: 新しい順、opaque cursor、重複なしでowner自身の4件だけが返る。
    def test_history_is_newest_first_owner_scoped_and_cursor_paginated(self):
        first = self.repository.list_history(self._query(limit=2))
        second = self.repository.list_history(self._query(limit=2, cursor=first.next_cursor))

        ids = [entry.operation.operation_id for entry in (*first.entries, *second.entries)]
        self.assertEqual(ids, [operation.operation_id for operation in reversed(self.operations)])
        self.assertNotIn(self.other_operation.operation_id, ids)
        self.assertTrue(first.has_more)
        self.assertFalse(second.has_more)
        self.assertNotIn(str(self.channel_id), first.next_cursor)

    # テストケース: 履歴のconfiguration snapshotを読み出す。
    # 期待値: 保存時のtemplate版・表示名・完全URLをcatalog変更なしでimmutable valueへ復元する。
    def test_history_restores_immutable_configuration_snapshot(self):
        page = self.repository.list_history(self._query(limit=1))
        configuration = page.entries[0].configuration
        self.assertEqual(configuration.reference.template_id, "jp-link-one")
        self.assertEqual(configuration.reference.version, 1)
        self.assertEqual(configuration.fields[0].display_name, "表示3")
        self.assertEqual(configuration.fields[0].uri, "https://example.com/3")
        self.assertNotIn("https://example.com/3", repr(page.entries[0]))

    # テストケース: 履歴件数を増やしてstate/history queryを実行する。
    # 期待値: query数が履歴件数に比例して増加しない。
    def test_projection_query_count_is_bounded(self):
        with CaptureQueriesContext(connection) as captured:
            self.repository.get_state(self._scope())
        self.assertLessEqual(len(captured), 7)

        with CaptureQueriesContext(connection) as captured:
            self.repository.list_history(self._query(limit=4))
        self.assertLessEqual(len(captured), 4)

    # テストケース: active operationまたはcleanup待ち資源があるstateをprojectする。
    # 期待値: 競合する新規APPLYをnext actionとして報告しない。
    def test_next_actions_do_not_offer_apply_during_active_or_cleanup_state(self):
        self.state.active_operation = self.operations[-1]
        self.state.save(update_fields=("active_operation",))
        active_view = self.repository.get_state(self._scope())
        self.assertNotIn("apply", {action.value for action in active_view.next_allowed_actions})

        self.state.active_operation = None
        self.state.save(update_fields=("active_operation",))
        ManagedRichMenu.objects.create(
            channel_state=self.state, origin_operation=self.operations[0],
            ownership_marker="lrm:v1:" + uuid4().hex,
            lifecycle="cleanup_required", image_digest="7" * 64,
        )
        cleanup_view = self.repository.get_state(self._scope())
        self.assertNotIn("apply", {action.value for action in cleanup_view.next_allowed_actions})

    def _scope(self):
        return OwnerChannelScope(
            owner_identity_public_id=self.owner,
            provider_id="0012345678",
            channel_public_id=self.channel_id,
        )

    def _query(self, *, limit, cursor=None):
        return HistoryQuery(scope=self._scope(), limit=limit, cursor=cursor)

    def _operation(self, index, owner=None):
        accepted_at = NOW + timedelta(minutes=index)
        operation = RichMenuOperation.objects.create(
            operation_id=uuid4(), channel_state=self.state,
            owner_identity_public_id=owner or self.owner, provider_id="0012345678",
            kind="apply", request_fingerprint=f"{index:x}" * 64,
            confirmation_usage_digest=(f"{(index + 1):x}" * 64)[:64],
            expected_channel_revision=NOW, status="succeeded", stage="verifying",
            result_code="succeeded",
            configuration_snapshot={
                "version": 1, "templateId": "jp-link-one", "templateVersion": 1,
                "fields": [{"displayName": f"表示{index}", "uri": f"https://example.com/{index}"}],
                "channelLabel": "履歴用チャネル",
            },
            accepted_at=accepted_at, completed_at=accepted_at,
        )
        RichMenuOperationTransition.objects.create(
            operation=operation, sequence=1, from_status="processing", to_status="succeeded",
            stage="verifying", safe_reason="succeeded", observed_at=accepted_at,
        )
        return operation
