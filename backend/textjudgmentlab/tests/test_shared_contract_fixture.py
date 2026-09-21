import json
from pathlib import Path

from django.test import SimpleTestCase

from textjudgmentlab.serializers import JudgmentRequestSerializer
from textjudgmentlab.views import LabAPIView, _success_payload

from .test_api_integration import _success


FIXTURE_PATH = Path("/test-fixtures/text-judgment-lab-contract-v1.json")


class SharedContractFixtureTests(SimpleTestCase):
    # テストケース: BackendとFrontendで共有するv1契約fixtureを読む。
    # 期待値: request、候補、Evidence、Score、errorの意味をBackend契約でも受理する。
    def test_shared_fixture_matches_backend_contract(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        serializer = JudgmentRequestSerializer(data=fixture["request"])

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(_success_payload(_success()), fixture["response"])
        self.assertEqual(fixture["response"]["contractVersion"], 1)
        self.assertEqual(
            tuple(fixture["response"]["details"]["choices"]),
            ("topic", "relevance", "change", "scope", "workaround", "result", "impact_evidence"),
        )
        self.assertEqual(fixture["response"]["details"]["score"]["legend"], {
            "0": "支障なし",
            "1": "不便だが別の操作で目的を達成できる",
            "2": "目的を達成できない",
        })
        self.assertEqual(fixture["response"]["evidence"]["scope"], {"kind": "unmentioned"})
        self.assertEqual(fixture["response"]["evidence"]["urgency"], {"kind": "known", "value": True})
        self.assertEqual(fixture["errors"]["judgmentUnavailable"], {
            "status": 502,
            "body": {"error": {"code": "judgment_failed", "message": "判定できませんでした。"}},
        })
        timeout = LabAPIView._error("judgment_timeout")
        self.assertEqual(timeout.status_code, fixture["errors"]["judgmentTimeout"]["status"])
        self.assertEqual(timeout.data, fixture["errors"]["judgmentTimeout"]["body"])
