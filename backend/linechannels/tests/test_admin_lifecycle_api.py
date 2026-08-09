from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from django.test import SimpleTestCase
from rest_framework.test import APIClient, APITestCase

from lineaccounts.authentication import OwnerPrincipal
from linechannels.admin_lifecycle_types import (
    DeactivationSummary,
    DeactivationFailed,
    DeactivationSucceeded,
    DeactivationView,
)
from linechannels.admin_presenters import AdminPresenter
from linechannels.admin_types import AdminChannelView


NOW = datetime(2026, 8, 3, 3, 4, 5, tzinfo=timezone.utc)
CHANNEL_ID = UUID("12345678-1234-4234-8234-123456789abc")
OPERATION_ID = UUID("22345678-1234-4234-8234-123456789abc")
RECOVERY_ID = UUID("32345678-1234-4234-8234-123456789abc")


def deactivation_view(**changes):
    values = dict(
        channel_public_id=CHANNEL_ID,
        channel_active=True,
        channel_revision=NOW,
        operation_id=OPERATION_ID,
        expected_channel_revision=NOW,
        status="confirmation_required",
        safe_reason="external_default",
        subject_rich_operation_id=None,
        latest_recovery_operation_id=None,
        accepted_at=NOW,
        updated_at=NOW,
        completed_at=None,
    )
    values.update(changes)
    return DeactivationView(**values)


class DeactivationPresenterTests(SimpleTestCase):
    # テストケース: 保存済みdeactivation viewをowner API presenterへ渡す。
    # 期待値: safe state・操作相関・次操作だけをexact DTOで返し、内部値を露出しない。
    def test_presents_exact_safe_deactivation_response(self):
        dto = AdminPresenter().deactivation(deactivation_view())

        self.assertEqual(
            set(dto),
            {
                "channelId", "channelActive", "channelUpdatedAt", "operationId",
                "status", "reason", "subjectOperationId", "recoveryOperationId",
                "nextAction", "acceptedAt", "updatedAt", "completedAt",
            },
        )
        self.assertEqual(dto["nextAction"], "resolve_external_default_then_recheck")
        self.assertNotIn("resource", str(dto).lower())
        self.assertNotIn("token", str(dto).lower())

    # テストケース: 未解決intentと再有効化直後のチャネルprojectionを表示する。
    # 期待値: 一覧・詳細に安全なsummaryとrefresh gateを含める。
    def test_channel_projection_includes_deactivation_summary_and_refresh_gate(self):
        summary = DeactivationSummary(
            OPERATION_ID,
            "confirmation_required",
            "external_default",
            NOW,
        )
        channel = AdminChannelView(
            CHANNEL_ID,
            "1234567890",
            "U" + "a" * 32,
            "通知チャネル",
            "0012345678",
            True,
            "configured",
            NOW,
            NOW,
            NOW,
            deactivation_summary=summary,
            rich_menu_refresh_required=True,
        )

        dto = AdminPresenter().channel(channel)

        self.assertEqual(dto["deactivationSummary"]["operationId"], str(OPERATION_ID))
        self.assertTrue(dto["richMenuRefreshRequired"])


class DeactivationAPITests(APITestCase):
    def setUp(self):
        self.origin = "https://test.example.ngrok.app"
        self.principal = OwnerPrincipal(uuid4(), uuid4(), "active")
        self.client = APIClient()
        self.client.force_authenticate(self.principal)
        self.coordinator = Mock()
        self.patcher = patch(
            "linechannels.admin_views.build_channel_deactivation_coordinator",
            return_value=self.coordinator,
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def post(self, path, body):
        return self.client.post(
            path, body, format="json", HTTP_ORIGIN=self.origin
        )

    # テストケース: owner endpointからstate取得、start、recheckを同じintentへ行う。
    # 期待値: owner・provider・revision fenceを通り、同じsafe intentを返す。
    def test_get_start_and_recheck_return_same_safe_intent(self):
        self.coordinator.get.return_value = DeactivationSucceeded(None)
        empty = self.client.get(f"/api/line/channels/{CHANNEL_ID}/deactivation/")

        self.coordinator.start.return_value = DeactivationSucceeded(deactivation_view())
        started = self.post(
            f"/api/line/channels/{CHANNEL_ID}/deactivation/",
            {"operationId": str(OPERATION_ID), "expectedUpdatedAt": NOW.isoformat()},
        )
        self.coordinator.recheck.return_value = DeactivationSucceeded(
            deactivation_view(
                latest_recovery_operation_id=RECOVERY_ID,
                safe_reason="stale_channel",
            )
        )
        rechecked = self.post(
            f"/api/line/channels/{CHANNEL_ID}/deactivation/recheck/",
            {
                "operationId": str(OPERATION_ID),
                "recoveryOperationId": str(RECOVERY_ID),
                "expectedUpdatedAt": NOW.isoformat(),
            },
        )

        self.assertEqual(empty.status_code, 200)
        self.assertIsNone(empty.json())
        self.assertEqual(started.json()["operationId"], str(OPERATION_ID))
        self.assertEqual(rechecked.json()["recoveryOperationId"], str(RECOVERY_ID))

    # テストケース: lifecycle serviceのstaleと競合結果をHTTP境界へ返す。
    # 期待値: 内部詳細を含まない固定statusとsafe error codeへ縮約する。
    def test_maps_lifecycle_conflicts_without_internal_details(self):
        self.coordinator.start.return_value = DeactivationFailed("deactivation_conflict")
        response = self.post(
            f"/api/line/channels/{CHANNEL_ID}/deactivation/",
            {"operationId": str(OPERATION_ID), "expectedUpdatedAt": NOW.isoformat()},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "deactivation_conflict")

    # テストケース: 非canonicalまたはUUIDv4以外のoperation correlationを送る。
    # 期待値: service開始前にinvalid inputとして拒否する。
    def test_rejects_noncanonical_or_non_v4_operation_ids(self):
        for operation_id in (str(OPERATION_ID).upper(), str(UUID(int=1))):
            with self.subTest(operation_id=operation_id):
                response = self.post(
                    f"/api/line/channels/{CHANNEL_ID}/deactivation/",
                    {
                        "operationId": operation_id,
                        "expectedUpdatedAt": NOW.isoformat(),
                    },
                )
                self.assertEqual(response.status_code, 400)
        self.coordinator.start.assert_not_called()
