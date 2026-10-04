import json
from pathlib import Path

from django.test import SimpleTestCase

from textjudgmentlab.serializers import JudgmentRequestSerializer
from textjudgmentlab.views import LabAPIView, _success_payload

from .test_api_integration import _success

FIXTURE_PATH = Path("/test-fixtures/text-judgment-lab-contract-v1.json")


class SharedContractFixtureTests(SimpleTestCase):
    # テストケース: BackendとFrontendで共有するv1契約fixtureを読む。
    # 期待値: v1の要求を拒否する。旧版の結果と固定のエラー形式は検証資料として保持する。
    def test_shared_v1_fixture_is_rejected_and_preserved(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        serializer = JudgmentRequestSerializer(data=fixture["request"])

        self.assertFalse(serializer.is_valid())
        self.assertEqual(_success_payload(_success())["contractVersion"], 2)
        self.assertEqual(fixture["response"]["contractVersion"], 1)
        self.assertEqual(
            tuple(fixture["response"]["details"]["choices"]),
            ("topic", "relevance", "change", "scope", "workaround", "result", "impact_evidence"),
        )
        self.assertEqual(
            fixture["response"]["details"]["score"]["legend"],
            {
                "0": "支障なし",
                "1": "不便だが別の操作で目的を達成できる",
                "2": "目的を達成できない",
            },
        )
        self.assertEqual(fixture["response"]["evidence"]["scope"], {"kind": "unmentioned"})
        self.assertEqual(
            fixture["response"]["evidence"]["urgency"], {"kind": "known", "value": True}
        )
        self.assertEqual(
            fixture["errors"]["judgmentUnavailable"],
            {
                "status": 502,
                "body": {"error": {"code": "judgment_failed", "message": "判定できませんでした。"}},
            },
        )
        timeout = LabAPIView._error("judgment_timeout")
        self.assertEqual(timeout.status_code, fixture["errors"]["judgmentTimeout"]["status"])
        self.assertEqual(timeout.data, fixture["errors"]["judgmentTimeout"]["body"])


class SharedV2ContractFixtureTests(SimpleTestCase):
    # テストケース: 2種類の相談の各質問について、v2の共有テストデータを実際の判定サービスへ渡す。
    # 期待値: 実際の送信内容と、公開する判定の閲覧情報、判定結果、全候補、正規化の理由が、定義済みの契約に一致する。
    def test_service_matches_shared_v2_contract(self) -> None:
        from datetime import UTC, datetime, timedelta
        from unittest.mock import patch

        from asgiref.sync import async_to_sync

        from textjudgmentlab.jev_gateway import JevTransportSuccess
        from textjudgmentlab.limits import LabLimits
        from textjudgmentlab.services import JudgmentService
        from textjudgmentlab.types import JudgmentSuccess, LabPrincipal

        from .test_services import _Gateway

        fixture = json.loads(
            Path("/test-fixtures/text-judgment-lab-contract-v2.json").read_text(encoding="utf-8")
        )
        now = datetime(2026, 10, 3, tzinfo=UTC)
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                serializer = JudgmentRequestSerializer(data=case["request"])
                self.assertTrue(serializer.is_valid(), serializer.errors)
                gateway = _Gateway(JevTransportSuccess(fixture["rawJudgment"], 25.0))
                service = JudgmentService(
                    model=fixture["rawJudgment"]["model"],
                    gateway=gateway,
                    limits=LabLimits(),
                    clock=lambda: now,
                )
                with patch("textjudgmentlab.services.monotonic", side_effect=[1.0, 1.025]):
                    result = async_to_sync(service.evaluate)(
                        LabPrincipal(now + timedelta(minutes=1), "safe-fixture-owner"),
                        serializer.to_request(),
                    )
                self.assertIsInstance(result, JudgmentSuccess)
                self.assertEqual(gateway.payloads, [case["sent"]])
                public = _success_payload(result)
                self.assertAlmostEqual(public["details"]["jevElapsedMs"], 25.0)
                public["details"]["jevElapsedMs"] = 25.0
                self.assertEqual(public, case["response"])
                self.assertEqual(public["inspection"]["state"], gateway.payloads[0]["state"])
                self.assertEqual(
                    public["inspection"]["questions"], gateway.payloads[0]["questions"]
                )
                self.assertNotIn("criteria", public["inspection"]["questions"]["urgency"])

    # テストケース: v2の共有テストデータに含まれる安全なエラー応答と旧版の要求を、Backendの入出力処理で検証する。
    # 期待値: 定義済みのHTTPステータスと応答本文を維持し、v1の要求を拒否する。
    def test_shared_errors_and_old_requests(self) -> None:
        fixture = json.loads(
            Path("/test-fixtures/text-judgment-lab-contract-v2.json").read_text(encoding="utf-8")
        )
        for code, expected in fixture["errors"].items():
            with self.subTest(code=code):
                response = LabAPIView._error(code)
                self.assertEqual(response.status_code, expected["status"])
                self.assertEqual(response.data, expected["body"])
        old_request = dict(fixture["cases"][0]["request"], contractVersion=1)
        self.assertFalse(JudgmentRequestSerializer(data=old_request).is_valid())
