from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import resolve, reverse
from rest_framework.test import APIClient

from textjudgmentlab.runtime import LabRuntimeConfigured, OwnerDigest, SecretValue
from textjudgmentlab.types import (
    Change,
    ChoiceDetail,
    JudgmentDetails,
    JudgmentEvidence,
    JudgmentFailure,
    LegacyJudgmentSuccess as JudgmentSuccess,
    KnownEvidence,
    LabPrincipal,
    NeedsReviewEvidence,
    NoulDetail,
    Relevance,
    Scope,
    ScoreDetail,
    Topic,
    UnmentionedEvidence,
    Workaround,
)


RUNTIME = LabRuntimeConfigured(
    channel_id="1234567890",
    owner_digest=OwnerDigest("a" * 64),
    origin="https://lab.example.test",
    api_key=SecretValue("api-key"),
    model="jev-1.13.0",
)
NOW = datetime(2026, 9, 21, 1, 2, 3, tzinfo=UTC)
CONSULTATION_ID = "11111111-1111-4111-8111-111111111111"
REQUEST_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"


def _request_payload() -> dict[str, object]:
    return {
        "contractVersion": 2,
        "consultationId": CONSULTATION_ID,
        "requestId": REQUEST_ID,
        "revision": 2,
        "text": "通知が届きません",
        "context": {
            "question": "start",
            "confirmed": {
                "topic": None,
                "scope": None,
                "workaround": None,
                "urgency": None,
            },
            "recentUserTexts": [],
            "impact": "unassessed",
        },
    }


def _choice(choice: str, candidates: tuple[str, ...]) -> ChoiceDetail:
    probabilities = {candidate: 0.0 for candidate in candidates}
    probabilities[choice] = 1.0
    return ChoiceDetail(choice, probabilities, 1.0)


def _success() -> JudgmentSuccess:
    return JudgmentSuccess(
        consultation_id=__import__("uuid").UUID(CONSULTATION_ID),
        request_id=__import__("uuid").UUID(REQUEST_ID),
        revision=2,
        model="jev-1.13.0",
        evidence=JudgmentEvidence(
            topic=KnownEvidence(Topic.MISSING_NOTIFICATION),
            relevance=Relevance.IN_SCOPE,
            change=Change.KEEP,
            scope=UnmentionedEvidence(),
            workaround=NeedsReviewEvidence(),
            result=UnmentionedEvidence(),
            impact="high",
            urgency=KnownEvidence(True),
        ),
        details=JudgmentDetails(
            choices={
                "topic": _choice("missing_notification", ("missing_notification", "notification_settings", "both", "unmentioned", "unclear")),
                "relevance": _choice("in_scope", ("in_scope", "mixed", "out_of_scope", "unclear")),
                "change": _choice("keep", ("keep", "restart", "unclear")),
                "scope": _choice("unmentioned", ("all", "specific", "unknown", "unmentioned", "unclear")),
                "workaround": _choice("unclear", ("can_read", "cannot_read", "unknown", "unmentioned", "unclear")),
                "result": _choice("unmentioned", ("done", "not_done", "not_tried", "cannot_check", "unmentioned", "unclear")),
                "impact_evidence": _choice("present", ("present", "absent", "unclear")),
            },
            score=ScoreDetail(
                1.75,
                {"0": "支障なし", "1": "不便だが別の操作で目的を達成できる", "2": "目的を達成できない"},
                {"0": 0.05, "1": 0.2, "2": 0.75},
                0.88,
            ),
            noul=NoulDetail(0.82),
            jev_elapsed_ms=321.4,
        ),
    )


class _Service:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    async def evaluate(self, principal, request):
        self.calls.append((principal, request))
        return self.result


@override_settings(TEXT_JUDGMENT_LAB_RUNTIME=RUNTIME)
class TextJudgmentLabApiIntegrationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        self.principal = LabPrincipal(datetime(2099, 1, 1, tzinfo=UTC), "a" * 64)
        self.client.force_authenticate(self.principal)
        self.headers = {"HTTP_ORIGIN": RUNTIME.origin}

    # テストケース: 専用APIのcanonical URLを解決する。
    # 期待値: 末尾slashなしのaccessとjudgmentsだけが専用Viewへ接続される。
    def test_registers_canonical_endpoints_without_slash_redirects(self) -> None:
        self.assertEqual(reverse("textjudgmentlab:access"), "/api/labs/text-judgment/access")
        self.assertEqual(reverse("textjudgmentlab:judgments"), "/api/labs/text-judgment/judgments")
        self.assertEqual(resolve("/api/labs/text-judgment/access").url_name, "access")
        self.assertEqual(self.client.post("/api/labs/text-judgment/access/", {}, format="json", **self.headers).status_code, 404)

    # テストケース: 本人principalで空JSONをaccessへ送る。
    # 期待値: token期限とserver時刻を固定schema・no-storeで返す。
    @patch("textjudgmentlab.views.timezone.now", return_value=NOW)
    def test_access_returns_expiry_and_server_time(self, _clock) -> None:
        response = self.client.post("/api/labs/text-judgment/access", {}, format="json", **self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            "status": "authorized",
            "expiresAt": "2099-01-01T00:00:00Z",
            "serverTime": "2026-09-21T01:02:03Z",
        })
        self.assertEqual(response["Cache-Control"], "no-store")

    # テストケース: 判定要求を送信しserviceが成功を返す。
    # 期待値: serializerを通したrequestを一度だけ評価し公開DTOへ変換する。
    def test_judgment_validates_calls_service_and_serializes_success(self) -> None:
        service = _Service(_success())
        with patch("textjudgmentlab.views.build_judgment_service", return_value=service):
            response = self.client.post(
                "/api/labs/text-judgment/judgments", _request_payload(), format="json", **self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(response.data["contractVersion"], 1)
        self.assertEqual(response.data["evidence"]["topic"], {"kind": "known", "value": "missing_notification"})
        self.assertEqual(response.data["details"]["score"]["legend"]["2"], "目的を達成できない")
        self.assertEqual(response["Cache-Control"], "no-store")

    # テストケース: serviceが各安全な失敗を返す。
    # 期待値: 固定HTTP statusとerror schemaへ変換し部分結果を返さない。
    def test_maps_service_failures_to_public_http_contract(self) -> None:
        cases = (
            ("rate_limited", 429, "rate_limited"),
            ("access_expired", 401, "reauthentication_required"),
            ("judge_unavailable", 502, "judgment_failed"),
            ("judge_timeout", 504, "judgment_timeout"),
            ("configuration_unavailable", 503, "access_unavailable"),
            ("unexpected", 500, "unexpected"),
        )
        for failure, status_code, public_code in cases:
            with self.subTest(failure=failure):
                with patch("textjudgmentlab.views.build_judgment_service", return_value=_Service(JudgmentFailure(failure))):
                    response = self.client.post(
                        "/api/labs/text-judgment/judgments", _request_payload(), format="json", **self.headers,
                    )
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.data["error"]["code"], public_code)
                self.assertEqual(set(response.data), {"error"})

    # テストケース: accessへ未知field、judgmentsへ不正文脈を送る。
    # 期待値: serviceを呼ばず400の固定schemaで拒否する。
    def test_rejects_unknown_access_input_and_invalid_judgment(self) -> None:
        access = self.client.post(
            "/api/labs/text-judgment/access", {"extra": True}, format="json", **self.headers,
        )
        service = _Service(_success())
        payload = _request_payload()
        payload["extra"] = True
        with patch("textjudgmentlab.views.build_judgment_service", return_value=service):
            judgment = self.client.post(
                "/api/labs/text-judgment/judgments", payload, format="json", **self.headers,
            )

        self.assertEqual(access.status_code, 400)
        self.assertEqual(judgment.status_code, 400)
        self.assertEqual(service.calls, [])
