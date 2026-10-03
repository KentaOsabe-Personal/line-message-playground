from uuid import UUID

from django.test import SimpleTestCase

from textjudgmentlab.jev_gateway import JevTransportSuccess
from textjudgmentlab.judgment_questions import QUESTION_IDS, build_jev_request
from textjudgmentlab.judgment_policy import normalize_judgment
from textjudgmentlab.types import (
    ConfirmedAnswers,
    Impact,
    JudgmentContext,
    JudgmentRequest,
    JudgmentFailure,
    LegacyJudgmentSuccess as JudgmentSuccess,
    KnownEvidence,
    NeedsReviewEvidence,
    QuestionId,
    Scope,
    Topic,
)


def _request() -> JudgmentRequest:
    return JudgmentRequest(
        contract_version=2,
        consultation_id=UUID("11111111-1111-4111-8111-111111111111"),
        request_id=UUID("22222222-2222-4222-8222-222222222222"),
        revision=4,
        text="  特定のトークだけ通知が来ません  ",
        context=JudgmentContext(
            question=QuestionId.SCOPE,
            confirmed=ConfirmedAnswers(
                topic=Topic.MISSING_NOTIFICATION,
                scope=Scope.SPECIFIC,
                workaround=None,
                urgency=False,
            ),
            recent_user_texts=("通知が来ません", "特定の相手です"),
            impact=Impact.NEEDS_REVIEW,
        ),
    )


class JudgmentQuestionsTests(SimpleTestCase):
    # テストケース: 自由文を一回のJev要求へ変換する。
    # 期待値: 9質問は独立した固定定義で、modelと全質問を一括送信する。
    def test_builds_all_fixed_questions_in_one_request(self) -> None:
        payload = build_jev_request(_request(), model="jev-1.13.0")

        self.assertEqual(payload["model"], "jev-1.13.0")
        self.assertEqual(tuple(payload["questions"]), QUESTION_IDS)
        self.assertEqual(len(payload["questions"]), 9)
        self.assertEqual(payload["questions"]["topic"]["type"], "choice")
        self.assertEqual(payload["questions"]["impact"]["type"], "score")
        self.assertEqual(payload["questions"]["urgency"]["type"], "noul")
        self.assertEqual(
            tuple(payload["questions"]["impact"]["criteria"]),
            (
                "支障なし",
                "不便だが別の操作で目的を達成できる",
                "目的を達成できない",
            ),
        )

    # テストケース: 確定回答と直近発言を含む相談文脈を構築する。
    # 期待値: 設計で許可された最小項目だけをstateへ含める。
    def test_state_contains_only_minimum_conversation_context(self) -> None:
        payload = build_jev_request(_request(), model="jev-1.13.0")

        self.assertEqual(
            payload["state"],
            {
                "currentText": "  特定のトークだけ通知が来ません  ",
                "questionId": "scope",
                "questionText": "通知が届かない範囲はどれですか？",
                "confirmed": {
                    "topic": "missing_notification",
                    "scope": "specific",
                    "workaround": None,
                    "urgency": False,
                },
                "impact": "needs_review",
                "recentUserTexts": ["通知が来ません", "特定の相手です"],
            },
        )
        serialized = repr(payload)
        self.assertNotIn("token", serialized.lower())
        self.assertNotIn("profile", serialized.lower())
        self.assertNotIn("consultationId", serialized)
        self.assertNotIn("requestId", serialized)

    # テストケース: 質問定義を生成後に書き換えようとする。
    # 期待値: 呼出し間で固定質問が汚染されない。
    def test_returns_fresh_question_payload(self) -> None:
        first = build_jev_request(_request(), model="jev-1.13.0")
        first["questions"]["topic"]["criteria"]["injected"] = "侵入"

        second = build_jev_request(_request(), model="jev-1.13.0")

        self.assertNotIn("injected", second["questions"]["topic"]["criteria"])

    # テストケース: 対象内・対象外が混在する入力向けの固定指示を確認する。
    # 期待値: 全9質問が対象内相談だけから範囲・回避策・結果を抽出する。
    def test_all_questions_include_complete_mixed_input_instruction(self) -> None:
        payload = build_jev_request(_request(), model="jev-1.13.0")
        required = (
            "範囲・回避策・結果は現在の対象内相談についてだけ抽出し、"
            "対象外の話題の回答を混ぜない。"
        )

        for question_id, question in payload["questions"].items():
            with self.subTest(question_id=question_id):
                self.assertIn(required, question["instructions"])


def _choice(choice: str, candidates: tuple[str, ...], confidence: float = 0.9):
    probabilities = {candidate: 0.0 for candidate in candidates}
    probabilities[choice] = 1.0
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities,
        "confidence": confidence,
    }


def _valid_answers() -> dict[str, object]:
    return {
        "topic": _choice(
            "missing_notification",
            (
                "missing_notification",
                "notification_settings",
                "both",
                "unmentioned",
                "unclear",
            ),
        ),
        "relevance": _choice(
            "in_scope", ("in_scope", "mixed", "out_of_scope", "unclear")
        ),
        "change": _choice("keep", ("keep", "restart", "unclear")),
        "scope": _choice(
            "specific", ("all", "specific", "unknown", "unmentioned", "unclear")
        ),
        "workaround": _choice(
            "can_read",
            ("can_read", "cannot_read", "unknown", "unmentioned", "unclear"),
        ),
        "result": _choice(
            "unmentioned",
            (
                "done",
                "not_done",
                "not_tried",
                "cannot_check",
                "unmentioned",
                "unclear",
            ),
        ),
        "impact_evidence": _choice(
            "present", ("present", "absent", "unclear")
        ),
        "impact": {
            "type": "score",
            "score": 1.5,
            "legend": {"0": "外部0", "1": "外部1", "2": "外部2"},
            "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
            "confidence": 0.7,
        },
        "urgency": {"type": "noul", "noul": 0.8},
    }


def _normalize(answers: dict[str, object]):
    return normalize_judgment(
        _request(),
        expected_model="jev-1.13.0",
        transport=JevTransportSuccess(
            payload={"model": "jev-1.13.0", "answers": answers},
            elapsed_ms=321.5,
        ),
    )


class JudgmentPolicyTests(SimpleTestCase):
    # テストケース: 完全な9回答を境界値で正規化する。
    # 期待値: Choice 0.70、Score 1.5、Noul 0.80を採用する。
    def test_normalizes_complete_response_at_inclusive_thresholds(self) -> None:
        result = _normalize(_valid_answers())

        self.assertIsInstance(result, JudgmentSuccess)
        assert isinstance(result, JudgmentSuccess)
        self.assertIsInstance(result.evidence.topic, KnownEvidence)
        self.assertEqual(result.evidence.topic.value, Topic.MISSING_NOTIFICATION)
        self.assertEqual(result.evidence.impact, "high")
        self.assertEqual(result.evidence.urgency, KnownEvidence(True))
        self.assertEqual(result.details.jev_elapsed_ms, 321.5)
        self.assertEqual(
            dict(result.details.score.legend),
            {
                "0": "支障なし",
                "1": "不便だが別の操作で目的を達成できる",
                "2": "目的を達成できない",
            },
        )
        self.assertEqual(result.details.score.score, 1.5)

    # テストケース: Choice、Score、Noulが採用閾値の内側にある。
    # 期待値: 候補を推測せず、対象項目だけ要確認へ正規化する。
    def test_normalizes_uncertain_values_to_needs_review(self) -> None:
        answers = _valid_answers()
        answers["scope"] = _choice(
            "specific",
            ("all", "specific", "unknown", "unmentioned", "unclear"),
            confidence=0.69,
        )
        answers["impact"]["confidence"] = 0.69
        answers["urgency"]["noul"] = 0.5

        result = _normalize(answers)

        self.assertIsInstance(result, JudgmentSuccess)
        assert isinstance(result, JudgmentSuccess)
        self.assertIsInstance(result.evidence.scope, NeedsReviewEvidence)
        self.assertEqual(result.evidence.impact, "needs_review")
        self.assertIsInstance(result.evidence.urgency, NeedsReviewEvidence)

    # テストケース: model不一致、回答欠損、未知候補を含む応答を受け取る。
    # 期待値: 部分結果を公開せず全体を安全な失敗にする。
    def test_rejects_incomplete_or_semantically_invalid_response(self) -> None:
        invalid_answers = _valid_answers()
        del invalid_answers["scope"]
        for transport in (
            JevTransportSuccess(
                payload={"model": "other-model", "answers": _valid_answers()},
                elapsed_ms=1,
            ),
            JevTransportSuccess(
                payload={"model": "jev-1.13.0", "answers": invalid_answers},
                elapsed_ms=1,
            ),
        ):
            with self.subTest(payload=transport.payload):
                result = normalize_judgment(
                    _request(),
                    expected_model="jev-1.13.0",
                    transport=transport,
                )

                self.assertEqual(result, JudgmentFailure("judge_unavailable"))

    # テストケース: 確率総和が許容差ちょうど0.01になる。
    # 期待値: 浮動小数の表現誤差に左右されず完全な応答として受理する。
    def test_accepts_probability_sum_at_documented_tolerance_boundary(self) -> None:
        answers = _valid_answers()
        answers["change"]["probabilities"] = {
            "keep": 0.70,
            "restart": 0.20,
            "unclear": 0.09,
        }

        result = _normalize(answers)

        self.assertIsInstance(result, JudgmentSuccess)

    # テストケース: 本人の「分からない」、未言及、否定を独立候補で返す。
    # 期待値: unknownは確定値、unmentionedは未言及、否定回答はその値として保つ。
    def test_preserves_unknown_unmentioned_and_negative_meanings(self) -> None:
        answers = _valid_answers()
        answers["scope"] = _choice(
            "unknown", ("all", "specific", "unknown", "unmentioned", "unclear")
        )
        answers["workaround"] = _choice(
            "cannot_read",
            ("can_read", "cannot_read", "unknown", "unmentioned", "unclear"),
        )
        answers["result"] = _choice(
            "unmentioned",
            (
                "done", "not_done", "not_tried", "cannot_check", "unmentioned", "unclear",
            ),
        )
        answers["impact"]["score"] = 1.49
        answers["urgency"]["noul"] = 0.20

        result = _normalize(answers)

        self.assertIsInstance(result, JudgmentSuccess)
        assert isinstance(result, JudgmentSuccess)
        self.assertEqual(result.evidence.scope, KnownEvidence(Scope.UNKNOWN))
        self.assertEqual(result.evidence.workaround.value, "cannot_read")
        self.assertEqual(result.evidence.result.kind, "unmentioned")
        self.assertEqual(result.evidence.impact, "low")
        self.assertEqual(result.evidence.urgency, KnownEvidence(False))

    # テストケース: NaN、boolean数値、未知候補、部分回答を個別に混入する。
    # 期待値: どの不正でも部分判定を公開せず全体失敗にする。
    def test_rejects_non_finite_boolean_unknown_and_partial_values(self) -> None:
        mutations = []
        nan_answers = _valid_answers()
        nan_answers["impact"]["score"] = float("nan")
        mutations.append(nan_answers)
        boolean_answers = _valid_answers()
        boolean_answers["urgency"]["noul"] = True
        mutations.append(boolean_answers)
        unknown_answers = _valid_answers()
        unknown_answers["scope"]["choice"] = "other"
        mutations.append(unknown_answers)
        partial_answers = _valid_answers()
        del partial_answers["urgency"]
        mutations.append(partial_answers)

        for answers in mutations:
            with self.subTest(answers=answers):
                self.assertEqual(
                    _normalize(answers), JudgmentFailure("judge_unavailable")
                )

    # テストケース: Choiceの最大確率0.70前後と同率最大を返す。
    # 期待値: 0.70以上の一意最大だけを採用し、それ以外は要確認にする。
    def test_choice_probability_threshold_and_unique_maximum(self) -> None:
        cases = (
            ({"all": 0.70, "specific": 0.10, "unknown": 0.10,
              "unmentioned": 0.05, "unclear": 0.05}, "known"),
            ({"all": 0.69, "specific": 0.11, "unknown": 0.10,
              "unmentioned": 0.05, "unclear": 0.05}, "needs_review"),
            ({"all": 0.40, "specific": 0.40, "unknown": 0.10,
              "unmentioned": 0.05, "unclear": 0.05}, "needs_review"),
        )
        for probabilities, expected_kind in cases:
            answers = _valid_answers()
            answers["scope"] = {
                "type": "choice",
                "choice": "all",
                "probabilities": probabilities,
                "confidence": 0.90,
            }

            result = _normalize(answers)

            self.assertIsInstance(result, JudgmentSuccess)
            assert isinstance(result, JudgmentSuccess)
            self.assertEqual(result.evidence.scope.kind, expected_kind)

    # テストケース: 対象外混在とimpact根拠なし・不明を返す。
    # 期待値: mixedは独立分類し、根拠が採用できないScoreは要確認にする。
    def test_mixed_relevance_and_missing_impact_evidence_stay_independent(self) -> None:
        for evidence_choice in ("absent", "unclear"):
            answers = _valid_answers()
            answers["relevance"] = _choice(
                "mixed", ("in_scope", "mixed", "out_of_scope", "unclear")
            )
            answers["impact_evidence"] = _choice(
                evidence_choice, ("present", "absent", "unclear")
            )

            result = _normalize(answers)

            self.assertIsInstance(result, JudgmentSuccess)
            assert isinstance(result, JudgmentSuccess)
            self.assertEqual(result.evidence.relevance.value, "mixed")
            self.assertEqual(result.evidence.impact, "needs_review")
