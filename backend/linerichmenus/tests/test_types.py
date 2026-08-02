from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

from django.test import SimpleTestCase

from linerichmenus.types import (
    ChannelStateView,
    CleanupRelation,
    DefaultRelation,
    DefaultObservation,
    HistoryEntry,
    HistoryPage,
    HistorySummary,
    ManagedResourceView,
    NextAllowedAction,
    NormalizedTemplate,
    ObservationKind,
    OperationKind,
    OperationCommand,
    OperationStage,
    OperationStatus,
    OperationView,
    PreviewCommand,
    PreviewView,
    PreviewWarning,
    ResourceLifecycle,
    SafeError,
    SafeResultCode,
    TemplateFieldValue,
    TemplateReference,
)


class RichMenuCommonTypeTests(SimpleTestCase):
    # テストケース: operation・stage・resource・observation・next actionのvariant集合を確認する。
    # 期待値: 設計済みvariantだけが閉じた列挙として公開される。
    def test_closed_variants_match_the_shared_contract(self):
        self.assertEqual(
            {kind.value for kind in OperationKind},
            {"apply", "unlink", "release", "recheck", "cleanup"},
        )
        self.assertEqual(
            {stage.value for stage in OperationStage},
            {
                "creating",
                "uploading",
                "setting_default",
                "verifying",
                "clearing_default",
                "cleaning",
                "local_release",
            },
        )
        self.assertEqual(
            {lifecycle.value for lifecycle in ResourceLifecycle},
            {
                "candidate",
                "applied",
                "old",
                "cleanup_required",
                "deleted",
                "released",
            },
        )
        with self.assertRaises(ValueError):
            OperationKind("unexpected")

    # テストケース: templateからchannel state/historyまでの共有値を組み立てる。
    # 期待値: SDK型や資格情報なしで同じimmutable値を全境界が受け渡せる。
    def test_shared_views_are_immutable_and_compose_without_external_types(self):
        now = datetime.now(UTC)
        operation_id = uuid4()
        resource_id = uuid4()
        template = NormalizedTemplate(
            reference=TemplateReference(template_id="jp-link-one", version=1),
            fields=(
                TemplateFieldValue(
                    display_name="公式サイト", uri="https://example.com/path?value=1"
                ),
            ),
        )
        operation = OperationView(
            operation_id=operation_id,
            kind=OperationKind.APPLY,
            status=OperationStatus.SUCCEEDED,
            stage=OperationStage.VERIFYING,
            result=SafeResultCode.SUCCEEDED,
            subject_operation_id=None,
            target_resource_id=None,
            accepted_at=now,
            completed_at=now,
            next_allowed_actions=(NextAllowedAction.UNLINK,),
        )
        resource = ManagedResourceView(
            public_id=resource_id,
            origin_operation_id=operation_id,
            lifecycle=ResourceLifecycle.APPLIED,
            image_digest="a" * 64,
        )
        observation = DefaultObservation(
            kind=ObservationKind.MANAGED_DEFAULT,
            observed_at=now,
            fingerprint="b" * 64,
            managed_resource_id=resource_id,
        )
        state = ChannelStateView(
            channel_public_id=uuid4(),
            current_resource=resource,
            blocking_operation=None,
            active_operation=None,
            cleanup_resources=(),
            latest_observation=observation,
            history_summary=HistorySummary(
                total_count=1,
                latest_operation_id=operation_id,
                latest_status=OperationStatus.SUCCEEDED,
            ),
            next_allowed_actions=(NextAllowedAction.UNLINK,),
        )
        history_channel_id = uuid4()
        history = HistoryPage(
            entries=(
                HistoryEntry(
                    operation=operation,
                    channel_public_id=history_channel_id,
                    channel_label="本番Bot",
                    configuration=template,
                    transitions=(SafeResultCode.ACCEPTED, SafeResultCode.SUCCEEDED),
                    default_relation=DefaultRelation.BECAME_DEFAULT,
                    cleanup_relation=CleanupRelation.NOT_REQUIRED,
                ),
            ),
            next_cursor=None,
            has_more=False,
        )

        self.assertEqual(state.current_resource, resource)
        self.assertEqual(history.entries[0].operation, operation)
        self.assertEqual(history.entries[0].channel_public_id, history_channel_id)
        self.assertEqual(state.history_summary.total_count, 1)
        with self.assertRaises(FrozenInstanceError):
            operation.status = OperationStatus.FAILED

    # テストケース: URLを持つimmutable configurationのデバッグ表現を確認する。
    # 期待値: 完全URLと表示名がreprへ露出せず、保持値はowner履歴用に維持される。
    def test_configuration_repr_redacts_full_url_and_display_name(self):
        field = TemplateFieldValue(
            display_name="秘密表示名",
            uri="https://example.com/private?token=canary",
        )
        template = NormalizedTemplate(
            reference=TemplateReference(template_id="jp-link-one", version=1),
            fields=(field,),
        )

        rendered = repr(template)

        self.assertNotIn("秘密表示名", rendered)
        self.assertNotIn("token=canary", rendered)
        self.assertEqual(template.fields[0].uri, field.uri)

    # テストケース: 下位例外が秘密・URL・binary・raw応答canaryを含む場合のsafe errorを作る。
    # 期待値: 固定codeとnext actionだけを保持し、生内容をerror/reprへ引き継がない。
    def test_safe_error_discards_unsafe_failure_details(self):
        unsafe = RuntimeError(
            "Bearer credential-canary https://example.com/private raw-response \x89PNG"
        )

        result = SafeError.from_untrusted(
            code=SafeResultCode.STORAGE_UNAVAILABLE,
            next_allowed_actions=(NextAllowedAction.GET_STATE,),
            error=unsafe,
        )

        self.assertEqual(result.code, SafeResultCode.STORAGE_UNAVAILABLE)
        rendered = repr(result)
        for canary in ("Bearer", "credential-canary", "https://", "raw-response", "PNG"):
            self.assertNotIn(canary, rendered)

    # テストケース: observationの管理対象relationがkindと矛盾する値を作る。
    # 期待値: 不正variantをimmutable境界で拒否する。
    def test_observation_relation_is_validated_at_type_boundary(self):
        with self.assertRaises(ValueError):
            DefaultObservation(
                kind=ObservationKind.MANAGED_DEFAULT,
                observed_at=datetime.now(UTC),
                fingerprint="a" * 64,
                managed_resource_id=None,
            )

    # テストケース: previewとoperationの共有command/resultをimmutable値として組み立てる。
    # 期待値: owner API・headless・repositoryが同じrelationとsafe preview情報を利用できる。
    def test_preview_and_operation_commands_share_closed_relations(self):
        now = datetime.now(UTC)
        channel_id = uuid4()
        target_id = uuid4()
        template = NormalizedTemplate(
            reference=TemplateReference(template_id="jp-link-one", version=1),
            fields=(
                TemplateFieldValue(
                    display_name="案内", uri="https://example.com/guide"
                ),
            ),
        )
        preview_command = PreviewCommand(
            channel_public_id=channel_id,
            expected_channel_revision=now,
            template=template,
        )
        preview = PreviewView(
            channel_public_id=channel_id,
            channel_label="本番Bot",
            template=template,
            image_digest="c" * 64,
            observation=DefaultObservation(
                kind=ObservationKind.DEFAULT_NONE,
                observed_at=now,
                fingerprint="d" * 64,
                managed_resource_id=None,
            ),
            expires_at=now,
            warnings=(PreviewWarning.URL_HISTORY_PERSISTED,),
        )
        command = OperationCommand(
            operation_id=uuid4(),
            channel_public_id=channel_id,
            expected_channel_revision=now,
            kind=OperationKind.UNLINK,
            subject_operation_id=None,
            target_resource_id=target_id,
        )

        self.assertEqual(preview_command.template, template)
        self.assertEqual(preview.image_digest, "c" * 64)
        self.assertEqual(command.target_resource_id, target_id)
        with self.assertRaises(ValueError):
            OperationCommand(
                operation_id=uuid4(),
                channel_public_id=channel_id,
                expected_channel_revision=now,
                kind=OperationKind.CLEANUP,
                subject_operation_id=None,
                target_resource_id=target_id,
            )
