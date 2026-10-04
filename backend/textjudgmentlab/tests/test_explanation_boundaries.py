import json
from copy import deepcopy
from pathlib import Path

from django.test import SimpleTestCase

from textjudgmentlab.jev_gateway import JevTransportSuccess
from textjudgmentlab.judgment_policy import normalize_judgment
from textjudgmentlab.judgment_questions import build_judgment_input
from textjudgmentlab.serializers import JudgmentRequestSerializer
from textjudgmentlab.types import JudgmentInspection, JudgmentSuccess, NormalizedJudgment
from textjudgmentlab.views import _success_payload


def merge_patch(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict) and "kind" not in value:
            merge_patch(target[key], value)
        else:
            target[key] = deepcopy(value)
    return target


class ExplanationBoundaryTests(SimpleTestCase):
    # テストケース: 固定した外部応答の条件を一つずつ変え、閾値の直前・一致・直後を確認する。同率最大・未言及・不明・支障根拠の不採用も確認する。
    # 期待値: 丸め前の値による判定とすべての理由が期待どおりになる。Frontendと共有する応答は、実際に正規化して公開した結果と一致する。
    def test_boundary_fixture_matches_real_policy_and_public_response(self):
        fixture = json.loads(
            Path("/test-fixtures/text-judgment-lab-boundaries-v2.json").read_text(encoding="utf-8")
        )
        serializer = JudgmentRequestSerializer(data=fixture["request"])
        serializer.is_valid(raise_exception=True)
        request = serializer.to_request()
        built = build_judgment_input(request, model="jev-1.13.0")
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                raw = deepcopy(fixture["rawJudgment"])
                merge_patch(raw["answers"], case["answerPatch"])
                result = normalize_judgment(
                    request, expected_model="jev-1.13.0", transport=JevTransportSuccess(raw, 25.0)
                )
                self.assertIsInstance(result, NormalizedJudgment)
                decision = result.normalization[case["field"]]
                self.assertEqual(decision.status, case["status"])
                self.assertEqual(set(decision.reasons), set(case["reasons"]))
                result = JudgmentSuccess(
                    request.consultation_id,
                    request.request_id,
                    request.revision,
                    "jev-1.13.0",
                    result.evidence,
                    result.details,
                    JudgmentInspection(
                        built.state, built.questions, result.policy, result.normalization
                    ),
                )
                expected = merge_patch(deepcopy(fixture["response"]), case["responsePatch"])
                self.assertEqual(_success_payload(result), expected)
