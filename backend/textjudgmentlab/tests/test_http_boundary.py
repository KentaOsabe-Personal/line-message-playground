import json

from django.test import SimpleTestCase, override_settings
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from textjudgmentlab.authentication import IsLabOwner, LabBearerAuthentication
from textjudgmentlab.runtime import LabRuntimeConfigured, OwnerDigest, SecretValue
from textjudgmentlab.views import LabAPIView

RUNTIME = LabRuntimeConfigured(
    channel_id="1234567890",
    owner_digest=OwnerDigest("a" * 64),
    origin="https://lab.example.test",
    api_key=SecretValue("api-key"),
    model="jev-1.13.0",
)


class ProbeLabView(LabAPIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        if request.data.get("explode"):
            raise RuntimeError("secret-canary")
        return Response({"ok": True})


@override_settings(TEXT_JUDGMENT_LAB_RUNTIME=RUNTIME)
class LabHttpBoundaryTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = ProbeLabView.as_view()

    # テストケース: canonical Originから小さいJSONをPOSTする
    # 期待値: 専用認証・permission・JSON parserが固定され、成功応答もno-storeになる
    def test_uses_dedicated_security_classes_and_no_store(self) -> None:
        self.assertEqual(LabAPIView.authentication_classes, [LabBearerAuthentication])
        self.assertEqual(LabAPIView.permission_classes, [IsLabOwner])
        self.assertEqual(LabAPIView.parser_classes, [JSONParser])
        response = self.view(
            self.factory.post(
                "/lab",
                {},
                format="json",
                HTTP_ORIGIN=RUNTIME.origin,
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")

    # テストケース: Origin欠落・不一致、過大body、過大AuthorizationでPOSTする
    # 期待値: parseやhandler実行前に固定schemaで拒否し、全応答をno-storeにする
    def test_rejects_origin_and_input_limits_with_safe_schema(self) -> None:
        cases = (
            ({}, b"{}", "origin_rejected", 403),
            ({"HTTP_ORIGIN": "https://other.example"}, b"{}", "origin_rejected", 403),
            (
                {"HTTP_ORIGIN": RUNTIME.origin},
                b'"' + b"x" * (32 * 1024) + b'"',
                "input_too_large",
                413,
            ),
            (
                {"HTTP_ORIGIN": RUNTIME.origin, "HTTP_AUTHORIZATION": "Bearer " + "x" * (8 * 1024)},
                b"{}",
                "reauthentication_required",
                401,
            ),
        )
        for headers, body, code, status_code in cases:
            with self.subTest(code=code):
                response = self.view(
                    self.factory.generic(
                        "POST",
                        "/lab",
                        body,
                        content_type="application/json",
                        **headers,
                    )
                )
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(set(response.data), {"error"})
                self.assertEqual(set(response.data["error"]), {"code", "message"})
                self.assertEqual(response.data["error"]["code"], code)
                self.assertEqual(response["Cache-Control"], "no-store")

    # テストケース: malformed JSON、非JSON、未対応method、想定外例外を送る
    # 期待値: owner schemaやdebug内容を漏らさず専用固定errorへ縮約する
    def test_normalizes_parser_media_method_and_unexpected_errors(self) -> None:
        requests = (
            (
                self.factory.generic(
                    "POST",
                    "/lab",
                    b"{",
                    content_type="application/json",
                    HTTP_ORIGIN=RUNTIME.origin,
                ),
                400,
                "invalid_input",
            ),
            (
                self.factory.generic(
                    "POST", "/lab", b"x", content_type="text/plain", HTTP_ORIGIN=RUNTIME.origin
                ),
                415,
                "unsupported_media_type",
            ),
            (self.factory.get("/lab", HTTP_ORIGIN=RUNTIME.origin), 405, "method_not_allowed"),
            (
                self.factory.post(
                    "/lab", {"explode": True}, format="json", HTTP_ORIGIN=RUNTIME.origin
                ),
                500,
                "unexpected",
            ),
        )
        for request, status_code, code in requests:
            with self.subTest(code=code):
                response = self.view(request)
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.data["error"]["code"], code)
                self.assertNotIn("secret-canary", json.dumps(response.data))
                self.assertEqual(response["Cache-Control"], "no-store")
