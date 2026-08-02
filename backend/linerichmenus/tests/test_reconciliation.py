from datetime import datetime, timezone
from uuid import uuid4

from django.test import SimpleTestCase

from linerichmenus.gateway import (
    GatewayUnknown,
    ImageObserved,
    RichMenuDefaultExternal,
    RichMenuDefaultNone,
    RichMenuDefaultPresent,
    RichMenuDefaultUnknown,
    ResourceAbsent,
    ResourceListAccepted,
    ResourceObserved,
    ResourceSummary,
)
from linerichmenus.reconciliation import (
    DefaultRichMenuReconciler,
    ManagedResourceTarget,
    ReconcileContext,
    RecheckContext,
)
from linerichmenus.types import (
    NextAllowedAction,
    ObservationKind,
    OperationStage,
    ResourceLifecycle,
)
from linerichmenus.gateway import RichMenuGatewayContext
from linechannels.types import AccessToken


SUBJECT_OPERATION_ID = uuid4()


class RecordingGateway:
    def __init__(self):
        self.default = RichMenuDefaultNone()
        self.resources = ResourceListAccepted(())
        self.resource = ResourceAbsent()
        self.image = ImageObserved(
            content_type="image/png",
            width=800,
            height=550,
            pixel_digest="a" * 64,
            byte_size=100,
        )
        self.calls = []

    def get_default(self, context):
        self.calls.append("get_default")
        return self.default

    def list_resources(self, context):
        self.calls.append("list_resources")
        return self.resources

    def get_resource(self, context, rich_menu_id):
        self.calls.append(("get_resource", rich_menu_id))
        return self.resource

    def download(self, context, rich_menu_id):
        self.calls.append(("download", rich_menu_id))
        return self.image

    def delete(self, context, rich_menu_id):
        raise AssertionError("recheck must never delete")


def gateway_context():
    return RichMenuGatewayContext(
        channel_public_id=uuid4(),
        channel_revision=datetime(2026, 8, 2, tzinfo=timezone.utc),
        access_token=AccessToken("access-token-canary"),
    )


def target(
    *,
    line_id="rich-menu-id-canary",
    lifecycle=ResourceLifecycle.APPLIED,
    marker="marker-canary",
    origin_operation_id=None,
    replacement_operation_id=None,
):
    return ManagedResourceTarget(
        public_id=uuid4(),
        line_rich_menu_id=line_id,
        lifecycle=lifecycle,
        ownership_marker=marker,
        origin_operation_id=origin_operation_id or uuid4(),
        replacement_operation_id=replacement_operation_id,
    )


class ReconciliationClassificationTests(SimpleTestCase):
    def setUp(self):
        self.gateway = RecordingGateway()
        self.reconciler = DefaultRichMenuReconciler(self.gateway)
        self.current = target()
        self.other = target(line_id="other-rich-menu-id", marker="other-marker")
        self.context = ReconcileContext(
            gateway_context=gateway_context(),
            current_resource=self.current,
            managed_resources=(self.current, self.other),
        )

    # テストケース: defaultなし、現在管理、別管理、外部、unknownを観測する。
    # 期待値: 保存済み関係だけをmanagedとし、外部resourceを取り込まない。
    def test_default_is_classified_without_importing_external_resource(self):
        cases = (
            (RichMenuDefaultNone(), ObservationKind.DEFAULT_NONE, None),
            (
                RichMenuDefaultPresent(self.current.line_rich_menu_id),
                ObservationKind.MANAGED_DEFAULT,
                self.current.public_id,
            ),
            (
                RichMenuDefaultPresent(self.other.line_rich_menu_id),
                ObservationKind.OTHER_MANAGED_DEFAULT,
                self.other.public_id,
            ),
            (RichMenuDefaultExternal(), ObservationKind.EXTERNAL_DEFAULT, None),
            (RichMenuDefaultUnknown("response_unknown"), ObservationKind.UNKNOWN, None),
        )
        for external, expected_kind, expected_resource in cases:
            with self.subTest(expected_kind=expected_kind):
                self.gateway.default = external
                result = self.reconciler.observe_channel(self.context)
                self.assertEqual(result.observation.kind, expected_kind)
                self.assertEqual(result.observation.managed_resource_id, expected_resource)
                if expected_kind is ObservationKind.UNKNOWN:
                    self.assertEqual(
                        result.next_allowed_actions,
                        (NextAllowedAction.RECHECK, NextAllowedAction.GET_STATE),
                    )

    # テストケース: released lifecycleのresource IDがdefaultとして返る。
    # 期待値: 既知IDでもexternal defaultへ分類し、managedへ戻さない。
    def test_released_resource_is_not_reclassified_as_managed(self):
        released = target(lifecycle=ResourceLifecycle.RELEASED)
        self.gateway.default = RichMenuDefaultPresent(released.line_rich_menu_id)
        context = ReconcileContext(
            gateway_context=gateway_context(),
            current_resource=released,
            managed_resources=(released,),
        )

        result = self.reconciler.observe_channel(context)

        self.assertEqual(result.observation.kind, ObservationKind.EXTERNAL_DEFAULT)
        self.assertIsNone(result.observation.managed_resource_id)


class RecheckObservationTests(SimpleTestCase):
    def setUp(self):
        self.gateway = RecordingGateway()
        self.reconciler = DefaultRichMenuReconciler(self.gateway)
        self.context = gateway_context()

    # テストケース: create unknown後にmarker一致が0件・1件・複数件となる。
    # 期待値: 完全一致1件だけを次stageへ進め、それ以外はunknownを維持する。
    def test_create_unknown_requires_one_exact_marker_match(self):
        cases = (
            ((), "ambiguous_resource"),
            ((ResourceSummary("one-id", "other-marker"),), "ambiguous_resource"),
            (
                (
                    ResourceSummary("one-id", "marker-canary"),
                    ResourceSummary("two-id", "marker-canary"),
                ),
                "ambiguous_resource",
            ),
        )
        for resources, expected_reason in cases:
            with self.subTest(resource_count=len(resources)):
                self.gateway.resources = ResourceListAccepted(resources)
                result = self.reconciler.recheck_operation(
                    RecheckContext(
                        gateway_context=self.context,
                        stage=OperationStage.CREATING,
                        subject_operation_id=SUBJECT_OPERATION_ID,
                        ownership_marker="marker-canary",
                    )
                )
                self.assertEqual(result.status, "unknown")
                self.assertEqual(result.reason, expected_reason)
        self.gateway.resources = ResourceListAccepted(
            (ResourceSummary("one-id", "marker-canary"),)
        )
        confirmed = self.reconciler.recheck_operation(
            RecheckContext(
                gateway_context=self.context,
                stage=OperationStage.CREATING,
                subject_operation_id=SUBJECT_OPERATION_ID,
                ownership_marker="marker-canary",
            )
        )
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.line_rich_menu_id, "one-id")

    # テストケース: create recheckのlist観測がrate limitedになる。
    # 期待値: 外部resourceを推測せず、observation_unknownを返す。
    def test_create_unknown_observation_failure_stays_unknown(self):
        self.gateway.resources = GatewayUnknown("rate_limited")
        result = self.reconciler.recheck_operation(
            RecheckContext(
                gateway_context=self.context,
                stage=OperationStage.CREATING,
                subject_operation_id=SUBJECT_OPERATION_ID,
                ownership_marker="marker-canary",
            )
        )
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.reason, "observation_unknown")

    # テストケース: upload unknown後にdownload digestを照合する。
    # 期待値: digest一致だけをupload確認とし、不一致は再uploadせずunknownにする。
    def test_upload_unknown_uses_download_digest_without_reupload(self):
        candidate = target(
            line_id="candidate-id", origin_operation_id=SUBJECT_OPERATION_ID
        )
        result = self.reconciler.recheck_operation(
            RecheckContext(
                gateway_context=self.context,
                stage=OperationStage.UPLOADING,
                subject_operation_id=SUBJECT_OPERATION_ID,
                candidate=candidate,
                expected_image_digest="a" * 64,
            )
        )
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(self.gateway.calls, [("download", "candidate-id")])

        self.gateway.calls.clear()
        self.gateway.image = ImageObserved(
            content_type="image/png",
            width=800,
            height=550,
            pixel_digest="b" * 64,
            byte_size=100,
        )
        rejected = self.reconciler.recheck_operation(
            RecheckContext(
                gateway_context=self.context,
                stage=OperationStage.UPLOADING,
                subject_operation_id=SUBJECT_OPERATION_ID,
                candidate=candidate,
                expected_image_digest="a" * 64,
            )
        )
        self.assertEqual(rejected.status, "unknown")
        self.assertEqual(rejected.reason, "not_confirmed")

    # テストケース: set/clear default unknownをdefault観測だけでrecheckする。
    # 期待値: 対象一致または対象非defaultを確認できた段階だけconfirmedにする。
    def test_set_and_clear_unknown_use_default_observation_only(self):
        candidate = target(
            line_id="candidate-id", origin_operation_id=SUBJECT_OPERATION_ID
        )
        self.gateway.default = RichMenuDefaultPresent("candidate-id")
        set_result = self.reconciler.recheck_operation(
            RecheckContext(
                gateway_context=self.context,
                stage=OperationStage.SETTING_DEFAULT,
                subject_operation_id=SUBJECT_OPERATION_ID,
                candidate=candidate,
            )
        )
        self.assertEqual(set_result.status, "confirmed")

        self.gateway.default = RichMenuDefaultNone()
        clear_result = self.reconciler.recheck_operation(
            RecheckContext(
                gateway_context=self.context,
                stage=OperationStage.CLEARING_DEFAULT,
                subject_operation_id=SUBJECT_OPERATION_ID,
                candidate=candidate,
            )
        )
        self.assertEqual(clear_result.status, "confirmed")

    # テストケース: cleanup unknownのget・list・default観測quorumを検証する。
    # 期待値: 3観測すべてが安全に確認できた場合だけdelete可能なconfirmedになる。
    def test_delete_unknown_requires_get_list_and_default_quorum(self):
        cleanup_target = target(
            line_id="cleanup-id",
            marker="cleanup-marker",
            lifecycle=ResourceLifecycle.CLEANUP_REQUIRED,
            origin_operation_id=SUBJECT_OPERATION_ID,
        )
        self.gateway.resource = ResourceAbsent()
        self.gateway.resources = ResourceListAccepted(())
        self.gateway.default = RichMenuDefaultNone()

        confirmed = self.reconciler.recheck_operation(
            RecheckContext(
                gateway_context=self.context,
                stage=OperationStage.CLEANING,
                subject_operation_id=SUBJECT_OPERATION_ID,
                target=cleanup_target,
            )
        )

        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(
            self.gateway.calls,
            [("get_resource", "cleanup-id"), "list_resources", "get_default"],
        )

        self.gateway.calls.clear()
        self.gateway.resources = ResourceListAccepted(
            (ResourceSummary("cleanup-id", "cleanup-marker"),)
        )
        unresolved = self.reconciler.recheck_operation(
            RecheckContext(
                gateway_context=self.context,
                stage=OperationStage.CLEANING,
                subject_operation_id=SUBJECT_OPERATION_ID,
                target=cleanup_target,
            )
        )
        self.assertEqual(unresolved.status, "unknown")
        self.assertEqual(unresolved.reason, "not_confirmed")

    # テストケース: cleanup targetのsubject relationとreplacement relationを検証する。
    # 期待値: 明示的な関係があるtargetだけをcleanup確認へ進める。
    def test_cleanup_requires_subject_relation_but_accepts_replacement_relation(self):
        cleanup_target = target(
            line_id="replacement-cleanup-id",
            marker="replacement-cleanup-marker",
            lifecycle=ResourceLifecycle.OLD,
            replacement_operation_id=SUBJECT_OPERATION_ID,
        )
        self.gateway.resource = ResourceAbsent()
        self.gateway.resources = ResourceListAccepted(())
        self.gateway.default = RichMenuDefaultNone()

        confirmed = self.reconciler.recheck_operation(
            RecheckContext(
                gateway_context=self.context,
                stage=OperationStage.CLEANING,
                subject_operation_id=SUBJECT_OPERATION_ID,
                target=cleanup_target,
            )
        )

        self.assertEqual(confirmed.status, "confirmed")
