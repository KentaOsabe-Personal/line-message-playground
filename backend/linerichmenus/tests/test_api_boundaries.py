from datetime import datetime, timezone
from uuid import uuid4

from django.test import SimpleTestCase

from linerichmenus.presenters import RichMenuPresenter
from linerichmenus.serializers import (
    HistoryQuerySerializer,
    OperationRequestSerializer,
    PreviewRequestSerializer,
)
from linerichmenus.services import ServiceFailed
from linerichmenus.types import InputFieldError, NextAllowedAction, SafeResultCode


NOW = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)


class RichMenuRequestBoundaryTests(SimpleTestCase):
    def operation(self, kind, **extra):
        body = {
            "kind": kind,
            "operationId": str(uuid4()),
            "channelRevision": NOW.isoformat(),
        }
        body.update(extra)
        return body

    # テストケース: preview requestへ未知fieldと不正なtemplate field shapeを渡す。
    # 期待値: strict serializerが両方をfield単位で拒否する。
    def test_preview_rejects_unknown_and_malformed_fields(self):
        serializer = PreviewRequestSerializer(
            data={
                "templateId": "jp-link-one",
                "templateVersion": 1,
                "channelRevision": NOW.isoformat(),
                "fields": {"area1": {"displayName": "案内", "uri": "https://example.com"}},
                "accessToken": "canary-secret",
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"request"})

    # テストケース: operation kindごとに許可されないvariant fieldを混在させる。
    # 期待値: apply以外のconfirmationやrelation不足を一律に拒否する。
    def test_operation_discriminated_union_rejects_variant_fields(self):
        target = str(uuid4())
        cases = (
            self.operation("apply", targetResourceId=target),
            self.operation("unlink", targetResourceId=target, confirmationToken="secret"),
            self.operation("release"),
            self.operation("recheck", subjectOperationId=str(uuid4()), targetResourceId=target),
            self.operation("cleanup", subjectOperationId=str(uuid4())),
        )

        for body in cases:
            with self.subTest(kind=body["kind"]):
                serializer = OperationRequestSerializer(data=body)
                self.assertFalse(serializer.is_valid())

    # テストケース: strict operation requestをdomain commandへ変換する。
    # 期待値: UUID・aware revision・variant relationが型付きcommandへ保持される。
    def test_operation_serializer_builds_domain_command(self):
        target = uuid4()
        serializer = OperationRequestSerializer(
            data=self.operation("unlink", targetResourceId=str(target))
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        command = serializer.to_command(uuid4())
        self.assertEqual(command.target_resource_id, target)
        self.assertEqual(command.kind.value, "unlink")

    # テストケース: history paginationへ未知queryまたは範囲外limitを渡す。
    # 期待値: owner scopeのqueryを作る前にstrict request境界で拒否する。
    def test_history_query_rejects_unknown_and_out_of_range_values(self):
        for data in ({"limit": 51}, {"limit": 20, "token": "canary-secret"}):
            with self.subTest(data=data):
                serializer = HistoryQuerySerializer(data=data)
                self.assertFalse(serializer.is_valid())


class RichMenuPresenterSafetyTests(SimpleTestCase):
    # テストケース: 未知のtemplate ID/versionでdomainのtemplateエラーを受け取る。
    # 期待値: API契約のtemplateIdへ明示変換し、安全な理由を保持する。
    def test_unknown_template_version_maps_domain_field_to_api_field(self):
        failure = ServiceFailed(
            SafeResultCode.INVALID_INPUT,
            errors=(InputFieldError("template", "unknown"),),
        )

        rendered = RichMenuPresenter().error(failure)

        self.assertEqual(
            rendered["error"]["fields"],
            [{"field": "templateId", "reason": "unknown"}],
        )

    # テストケース: domain errorに未知の外側canary fieldだけが含まれる。
    # 期待値: canaryを反射せず固定requestエラーへfail closedする。
    def test_unknown_outer_error_field_falls_back_to_safe_request_error(self):
        canary = "CanaryOuterSecret123"
        failure = ServiceFailed(
            SafeResultCode.INVALID_INPUT,
            errors=(InputFieldError(canary, "unexpected"),),
        )

        rendered = RichMenuPresenter().error(failure)

        self.assertEqual(
            rendered["error"]["fields"],
            [{"field": "request", "reason": "invalid"}],
        )
        self.assertNotIn(canary, repr(rendered))

    # テストケース: untrusted errorへ秘密値を含む例外を与える。
    # 期待値: safe error presenterは固定code/actionだけを返しreprにも秘密を含めない。
    def test_safe_error_never_exposes_untrusted_values(self):
        canary = "canary-token https://secret.example/raw"
        failure = ServiceFailed(
            SafeResultCode.STORAGE_UNAVAILABLE,
            next_allowed_actions=(NextAllowedAction.RECHECK,),
            errors=(InputFieldError("https://secret.example/raw", "unexpected"),),
        )

        rendered = RichMenuPresenter().error(failure, error=RuntimeError(canary))

        self.assertEqual(
            rendered,
            {
                "error": {
                    "code": "storage_unavailable",
                    "nextAllowedActions": ["recheck"],
                    "fields": [{"field": "request", "reason": "invalid"}],
                }
            },
        )
        self.assertNotIn(canary, repr(rendered))
