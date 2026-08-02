from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from lineaccounts.authentication import OwnerPrincipal
from linerichmenus.services import OperationSucceeded, ServiceFailed, StateSucceeded
from linerichmenus.services import TemplateListSucceeded
from linerichmenus.catalog import DefaultTemplateCatalog
from linerichmenus.types import (
    ChannelStateView,
    HistorySummary,
    NextAllowedAction,
    OperationKind,
    OperationStatus,
    OperationView,
    SafeResultCode,
)
from linerichmenus.views import (
    ChannelHistoryAPIView,
    ChannelOperationAPIView,
    ChannelPreviewAPIView,
    ChannelStateAPIView,
    OperationDetailAPIView,
    TemplateListAPIView,
)


NOW = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)


class OwnerRichMenuAPITests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.principal = OwnerPrincipal(uuid4(), uuid4(), "active")
        self.channel_id = uuid4()

    def request(self, method, path, body=None):
        request = getattr(self.factory, method)(
            path,
            body,
            format="json",
            HTTP_ORIGIN="https://test.example.ngrok.app",
        )
        request._dont_enforce_csrf_checks = True
        force_authenticate(request, user=self.principal)
        return request

    # テストケース: active ownerが組み込みtemplate一覧を参照する。
    # 期待値: 3種類の版・geometry・input limitだけを返す。
    def test_template_list_is_owner_protected_and_exact(self):
        service = Mock()
        service.list_templates.return_value = TemplateListSucceeded(
            DefaultTemplateCatalog().list_templates()
        )
        with patch("linerichmenus.views.build_rich_menu_service", return_value=service):
            response = TemplateListAPIView.as_view()(
                self.request("get", "/api/line/rich-menus/templates/")
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["items"]), 3)
        self.assertEqual(
            set(response.data["items"][0]),
            {"templateId", "version", "displayName", "canvas", "areas", "requiredFields", "limits"},
        )

    # テストケース: ownerがstrict preview endpointへ確認入力を送る。
    # 期待値: owner context・channel ID・aware revisionをserviceへ渡しsafe failureへ対応する。
    def test_preview_endpoint_builds_typed_command_and_maps_safe_failure(self):
        service = Mock()
        service.preview.return_value = ServiceFailed(SafeResultCode.STALE_CHANNEL)
        body = {
            "templateId": "jp-link-one",
            "templateVersion": 1,
            "channelRevision": NOW.isoformat(),
            "fields": {"area1": {"displayName": "案内", "uri": "https://example.com"}},
        }
        with patch("linerichmenus.views.build_rich_menu_service", return_value=service):
            response = ChannelPreviewAPIView.as_view()(
                self.request("post", "/preview/", body), channel_id=self.channel_id
            )

        self.assertEqual(response.status_code, 409)
        command = service.preview.call_args.args[1]
        self.assertEqual(command.channel_public_id, self.channel_id)
        self.assertEqual(command.expected_channel_revision, NOW)
        self.assertEqual(response.data["error"]["code"], "stale_channel")

    # テストケース: preview endpointへ秘密をfield名に埋めた未知keyを送る。
    # 期待値: field名を反射せずAPI固有invalid_inputへ安全に縮約する。
    def test_unknown_request_field_is_secret_free_invalid_input(self):
        canary = "CanarySecretValue123"
        body = {
            "templateId": "jp-link-one",
            "templateVersion": 1,
            "channelRevision": NOW.isoformat(),
            "fields": {"area1": {"displayName": "案内", "uri": "https://example.com"}},
            canary: "raw-secret",
        }

        response = ChannelPreviewAPIView.as_view()(
            self.request("post", "/preview/", body), channel_id=self.channel_id
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "invalid_input")
        self.assertNotIn(canary, repr(response.data))
        self.assertEqual(
            response.data["error"]["fields"],
            [{"field": "request", "reason": "invalid"}],
        )

    # テストケース: 既知のtemplate項目欠落と未知のnested canary keyを同時に送る。
    # 期待値: canaryを反射せず、既知項目の安全なpathとrequired理由だけを保持する。
    def test_known_nested_field_error_keeps_safe_field_level_reason(self):
        canary = "CanaryNestedSecret123"
        body = {
            "templateId": "jp-link-one",
            "templateVersion": 1,
            "channelRevision": NOW.isoformat(),
            "fields": {
                "area1": {
                    "uri": "https://example.com",
                    canary: "raw-secret",
                }
            },
        }

        response = ChannelPreviewAPIView.as_view()(
            self.request("post", "/preview/", body), channel_id=self.channel_id
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "invalid_input")
        self.assertNotIn(canary, repr(response.data))
        self.assertIn(
            {"field": "area1.displayName", "reason": "required"},
            response.data["error"]["fields"],
        )

    # テストケース: inactive channelの保存projectionをstate endpointで読む。
    # 期待値: serviceのsafe DTOだけを返しHTTP層は外部観測を追加しない。
    def test_state_endpoint_presents_saved_projection(self):
        service = Mock()
        service.get_state.return_value = StateSucceeded(
            ChannelStateView(
                channel_public_id=self.channel_id,
                current_resource=None,
                blocking_operation=None,
                active_operation=None,
                cleanup_resources=(),
                latest_observation=None,
                history_summary=HistorySummary(0, None, None),
                next_allowed_actions=(),
            )
        )
        with patch("linerichmenus.views.build_rich_menu_service", return_value=service):
            response = ChannelStateAPIView.as_view()(
                self.request("get", "/state/"), channel_id=self.channel_id
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["channelId"], str(self.channel_id))
        self.assertNotIn("confirmationToken", response.data)

    # テストケース: 全mutation variantが一つのoperation endpointへ到達する。
    # 期待値: discriminated commandとowner contextを渡し保存済みOperationResponseを返す。
    def test_operation_endpoint_routes_all_kinds_to_same_service_method(self):
        variants = {
            "apply": {
                "confirmationToken": "opaque-confirmation",
                "templateId": "jp-link-one",
                "templateVersion": 1,
                "fields": {
                    "area1": {
                        "displayName": "案内",
                        "uri": "https://example.com",
                    }
                },
            },
            "unlink": {"targetResourceId": str(uuid4())},
            "release": {"targetResourceId": str(uuid4())},
            "recheck": {"subjectOperationId": str(uuid4())},
            "cleanup": {
                "subjectOperationId": str(uuid4()),
                "targetResourceId": str(uuid4()),
            },
        }
        for kind, fields in variants.items():
            operation = self.operation(kind)
            service = Mock()
            service.start_operation.return_value = OperationSucceeded(operation)
            body = {
                "kind": kind,
                "operationId": str(operation.operation_id),
                "channelRevision": NOW.isoformat(),
                **fields,
            }
            with self.subTest(kind=kind), patch(
                "linerichmenus.views.build_rich_menu_service", return_value=service
            ):
                response = ChannelOperationAPIView.as_view()(
                    self.request("post", "/operations/", body),
                    channel_id=self.channel_id,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["kind"], kind)
                self.assertEqual(service.start_operation.call_count, 1)

    # テストケース: operation/historyの別scope参照がserviceで不存在へ分類される。
    # 期待値: HTTPは対象種別を区別しない404を返す。
    def test_operation_and_history_not_found_are_safe_404(self):
        service = Mock()
        service.get_operation.return_value = ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        service.list_history.return_value = ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        with patch("linerichmenus.views.build_rich_menu_service", return_value=service):
            operation_response = OperationDetailAPIView.as_view()(
                self.request("get", "/operations/x/"), operation_id=uuid4()
            )
            history_response = ChannelHistoryAPIView.as_view()(
                self.request("get", "/history/?limit=20"), channel_id=self.channel_id
            )

        self.assertEqual(operation_response.status_code, 404)
        self.assertEqual(history_response.status_code, 404)

    def operation(self, kind):
        return OperationView(
            operation_id=uuid4(),
            kind=OperationKind(kind),
            status=OperationStatus.SUCCEEDED,
            stage=None,
            result=SafeResultCode.SUCCEEDED,
            subject_operation_id=None if kind not in {"recheck", "cleanup"} else uuid4(),
            target_resource_id=None if kind in {"apply", "recheck"} else uuid4(),
            accepted_at=NOW,
            completed_at=NOW,
            next_allowed_actions=(NextAllowedAction.RECHECK,),
        )
