from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from linechannels.models import LineChannel
from linerichmenus.catalog import DefaultTemplateCatalog
from linerichmenus.models import (
    ManagedRichMenu,
    RichMenuChannelState,
    RichMenuOperation,
    RichMenuOperationTransition,
)
from linerichmenus.repository import (
    DjangoRichMenuRepository,
    HistoryQuery,
    OwnerChannelScope,
)
from linerichmenus.reconciliation import DefaultRichMenuReconciler, RecheckContext
from linerichmenus.renderer import DefaultDeterministicRenderer
from linerichmenus.types import (
    OperationStage,
    ResourceLifecycle,
    TemplateInput,
    TemplateReference,
)

from .test_reconciliation import (
    SUBJECT_OPERATION_ID,
    RecordingGateway,
    gateway_context,
    target,
)


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class RichMenuExternalCallBudgetTests(SimpleTestCase):
    # テストケース: create unknownの明示recheckを一回だけ実行する。
    # 期待値: listは最大一回で、自動polling・create再試行・他endpoint呼出を行わない。
    def test_create_recheck_uses_one_list_without_polling(self):
        gateway = RecordingGateway()
        reconciler = DefaultRichMenuReconciler(gateway)

        result = reconciler.recheck_operation(
            RecheckContext(
                gateway_context=gateway_context(),
                stage=OperationStage.CREATING,
                subject_operation_id=SUBJECT_OPERATION_ID,
                ownership_marker="marker-performance-canary",
            )
        )

        self.assertEqual(result.status, "unknown")
        self.assertEqual(gateway.calls, ["list_resources"])

    # テストケース: delete unknownの明示recheckを一回だけ実行する。
    # 期待値: get・list・defaultを各一回だけ観測し、deleteや自動再試行を行わない。
    def test_cleanup_recheck_stays_within_observation_quorum_budget(self):
        gateway = RecordingGateway()
        reconciler = DefaultRichMenuReconciler(gateway)
        cleanup_target = target(
            line_id="cleanup-performance-canary",
            marker="marker-performance-canary",
            lifecycle=ResourceLifecycle.CLEANUP_REQUIRED,
            origin_operation_id=SUBJECT_OPERATION_ID,
        )

        reconciler.recheck_operation(
            RecheckContext(
                gateway_context=gateway_context(),
                stage=OperationStage.CLEANING,
                subject_operation_id=SUBJECT_OPERATION_ID,
                target=cleanup_target,
            )
        )

        self.assertEqual(
            gateway.calls,
            [
                ("get_resource", "cleanup-performance-canary"),
                "list_resources",
                "get_default",
            ],
        )

    # テストケース: 最大入力を一件のrequest内画像として生成する。
    # 期待値: 画像は一件かつLINE上限1MB以内で、追加画像や外部I/Oを必要としない。
    def test_single_render_stays_within_one_megabyte_request_budget(self):
        catalog = DefaultTemplateCatalog()
        template = catalog.normalize(
            TemplateInput(
                TemplateReference("jp-link-three", 1),
                {
                    "area1": {"displayName": "あ" * 20, "uri": "https://example.com/1"},
                    "area2": {"displayName": "い" * 20, "uri": "https://example.com/2"},
                    "area3": {"displayName": "う" * 20, "uri": "https://example.com/3"},
                },
            )
        )
        self.assertEqual(type(template).__name__, "NormalizedTemplate")

        rendered = DefaultDeterministicRenderer(catalog=catalog).render(template)

        self.assertEqual(type(rendered).__name__, "RenderedImage")
        self.assertLessEqual(len(rendered.binary), 1_000_000)


class RichMenuQueryBudgetTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.channel_id = uuid4()
        self.owner = uuid4()
        LineChannel.objects.create(
            public_id=self.channel_id,
            messaging_api_channel_id="1234567890",
            bot_user_id="U" + uuid4().hex,
            label="性能検証チャネル",
            provider_id="0012345678",
            is_active=True,
        )
        self.state = RichMenuChannelState.objects.create(
            channel_public_id=self.channel_id,
            last_observation_kind="default_none",
            last_observation_fingerprint="9" * 64,
            last_observed_at=NOW,
        )
        self.operations = [self._operation(index) for index in range(20)]
        resource = ManagedRichMenu.objects.create(
            channel_state=self.state,
            origin_operation=self.operations[0],
            ownership_marker="lrm:v1:" + uuid4().hex,
            lifecycle="applied",
            image_digest="8" * 64,
        )
        self.state.current_resource = resource
        self.state.save(update_fields=("current_resource",))

    # テストケース: 履歴件数を増やしたstate/history readを専用performance suiteで再検証する。
    # 期待値: query数が件数に比例せず、定義済みbudget内に収まる。
    def test_state_and_history_queries_keep_constant_budget(self):
        repository = DjangoRichMenuRepository()

        with CaptureQueriesContext(connection) as state_queries:
            repository.get_state(self._scope())
        with CaptureQueriesContext(connection) as history_queries:
            repository.list_history(self._query(limit=4))

        self.assertLessEqual(len(state_queries), 7)
        self.assertLessEqual(len(history_queries), 4)

    def _scope(self):
        return OwnerChannelScope(
            owner_identity_public_id=self.owner,
            provider_id="0012345678",
            channel_public_id=self.channel_id,
        )

    def _query(self, *, limit):
        return HistoryQuery(scope=self._scope(), limit=limit, cursor=None)

    def _operation(self, index):
        accepted_at = NOW + timedelta(minutes=index)
        operation = RichMenuOperation.objects.create(
            operation_id=uuid4(),
            channel_state=self.state,
            owner_identity_public_id=self.owner,
            provider_id="0012345678",
            kind="apply",
            request_fingerprint=sha256(f"request-{index}".encode()).hexdigest(),
            confirmation_usage_digest=sha256(
                f"confirmation-{index}".encode()
            ).hexdigest(),
            expected_channel_revision=NOW,
            status="succeeded",
            stage="verifying",
            result_code="succeeded",
            configuration_snapshot={
                "version": 1,
                "templateId": "jp-link-one",
                "templateVersion": 1,
                "fields": [
                    {
                        "displayName": f"表示{index}",
                        "uri": f"https://example.com/{index}",
                    }
                ],
                "channelLabel": "性能検証チャネル",
            },
            accepted_at=accepted_at,
            completed_at=accepted_at,
        )
        RichMenuOperationTransition.objects.create(
            operation=operation,
            sequence=1,
            from_status="processing",
            to_status="succeeded",
            stage="verifying",
            safe_reason="succeeded",
            observed_at=accepted_at,
        )
        return operation
