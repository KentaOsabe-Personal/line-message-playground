from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

from django.test import SimpleTestCase

from textjudgmentlab.serializers import JudgmentRequestSerializer
from textjudgmentlab.types import (
    Impact,
    LabPrincipal,
    QuestionId,
    Scope,
    Topic,
)


class LabContractTests(SimpleTestCase):
    consultation_id = "11111111-1111-4111-8111-111111111111"
    request_id = "abcdefab-cdef-4abc-8def-abcdefabcdef"

    def valid_payload(self) -> dict[str, object]:
        return {
            "contractVersion": 2,
            "consultationId": self.consultation_id,
            "requestId": self.request_id,
            "revision": 0,
            "text": " LINEの通知が届きません ",
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

    # テストケース: exactな入力を型付き要求へ変換する。
    # 期待値: trim済み本文、UUID、固定enum、immutableな文脈だけがservice境界へ渡る。
    def test_builds_an_immutable_typed_request_from_exact_input(self):
        serializer = JudgmentRequestSerializer(data=self.valid_payload())

        self.assertTrue(serializer.is_valid(), serializer.errors)
        request = serializer.to_request()
        self.assertEqual(request.contract_version, 2)
        self.assertEqual(request.text, "LINEの通知が届きません")
        self.assertEqual(request.context.question, QuestionId.START)
        self.assertEqual(request.context.impact, Impact.UNASSESSED)
        self.assertEqual(str(request.consultation_id), self.consultation_id)
        with self.assertRaises(FrozenInstanceError):
            request.revision = 1  # type: ignore[misc]

    # テストケース: 未知キー、非canonical UUID、bool revision、未知enumを渡す。
    # 期待値: 曖昧なcoercionを行わず全て拒否する。
    def test_rejects_unknown_keys_identifiers_numbers_and_enums(self):
        cases = []
        extra = self.valid_payload()
        extra["token"] = "secret"
        cases.append(extra)
        uppercase_uuid = self.valid_payload()
        uppercase_uuid["requestId"] = self.request_id.upper()
        cases.append(uppercase_uuid)
        bool_revision = self.valid_payload()
        bool_revision["revision"] = True
        cases.append(bool_revision)
        unknown_question = self.valid_payload()
        unknown_question["context"] = {
            **unknown_question["context"],  # type: ignore[arg-type]
            "question": "other",
        }
        cases.append(unknown_question)

        for payload in cases:
            serializer = JudgmentRequestSerializer(data=payload)
            self.assertFalse(serializer.is_valid(), payload)

    # テストケース: emojiを含む1000/1001 Unicode code pointと空白本文を渡す。
    # 期待値: UTF-16長ではなくcode pointで1000まで受理し、超過と空白だけを拒否する。
    def test_validates_text_by_unicode_code_points(self):
        accepted = self.valid_payload()
        accepted["text"] = "😀" * 1000
        rejected = self.valid_payload()
        rejected["text"] = "😀" * 1001
        blank = self.valid_payload()
        blank["text"] = " \n "

        accepted_serializer = JudgmentRequestSerializer(data=accepted)
        self.assertTrue(accepted_serializer.is_valid(), accepted_serializer.errors)
        self.assertFalse(JudgmentRequestSerializer(data=rejected).is_valid())
        self.assertFalse(JudgmentRequestSerializer(data=blank).is_valid())

    # テストケース: 設定相談へ回避策を混在、topic未確定で結果質問、履歴3件を渡す。
    # 期待値: 意味的に矛盾する文脈と過剰な履歴を400相当のvalidation errorにする。
    def test_rejects_contradictory_context_and_excess_history(self):
        settings_workaround = self.valid_payload()
        settings_workaround["context"] = {
            "question": "scope",
            "confirmed": {
                "topic": "notification_settings",
                "scope": None,
                "workaround": "can_read",
                "urgency": None,
            },
            "recentUserTexts": [],
            "impact": "low",
        }
        result_without_topic = self.valid_payload()
        result_without_topic["context"] = {
            **result_without_topic["context"],  # type: ignore[arg-type]
            "question": "result",
        }
        excessive_history = self.valid_payload()
        excessive_history["context"] = {
            **excessive_history["context"],  # type: ignore[arg-type]
            "recentUserTexts": ["a", "b", "c"],
        }

        for payload in (settings_workaround, result_without_topic, excessive_history):
            self.assertFalse(JudgmentRequestSerializer(data=payload).is_valid())

    # テストケース: 本人principalとenumを生成する。
    # 期待値: principalはimmutableで、raw tokenやsubjectを保持せず期限だけを判定できる。
    def test_principal_and_fixed_enums_are_closed_and_immutable(self):
        now = datetime.now(UTC)
        principal = LabPrincipal(expires_at=now + timedelta(minutes=1), owner_digest="b" * 64)

        self.assertTrue(principal.is_valid_at(now))
        self.assertFalse(principal.is_valid_at(now + timedelta(minutes=2)))
        self.assertEqual(Topic.MISSING_NOTIFICATION.value, "missing_notification")
        self.assertEqual(Scope.UNKNOWN.value, "unknown")
        self.assertNotIn("token", repr(principal).lower())

    # テストケース: 認証済みの利用者がv1の判定要求を送信する。
    # 期待値: 判定サービスを呼び出さず、HTTP 400とCache-Control: no-storeを返す。
    def test_rejects_v1_at_the_http_boundary(self):
        from unittest.mock import patch
        from rest_framework.test import APIRequestFactory, force_authenticate
        from django.test import override_settings
        from textjudgmentlab.views import LabJudgmentAPIView
        from .test_http_boundary import RUNTIME

        payload = self.valid_payload()
        payload["contractVersion"] = 1
        request = APIRequestFactory().post(
            "/api/labs/text-judgment/judgments", payload, format="json",
            HTTP_ORIGIN=RUNTIME.origin,
        )
        force_authenticate(request, user=LabPrincipal(datetime.now(UTC) + timedelta(minutes=1), "a" * 64))
        with override_settings(TEXT_JUDGMENT_LAB_RUNTIME=RUNTIME), patch(
            "textjudgmentlab.views.build_judgment_service"
        ) as service:
            response = LabJudgmentAPIView.as_view()(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "invalid_input")
        self.assertEqual(response["Cache-Control"], "no-store")
        service.assert_not_called()

    # テストケース: v2の閲覧情報を作成した後、元のマップと配列を変更する。
    # 期待値: 質問、理由、比較記録は作成時の値を保持する。Noulにcriteriaは定義しない。
    def test_inspection_values_defensively_copy_nested_collections(self):
        from textjudgmentlab.types import (
            AdoptionPolicySnapshot, ChoiceAdoptionPolicy, ScoreAdoptionPolicy,
            NoulAdoptionPolicy, PolicyCheck, NormalizationDecision,
            JudgmentStateSnapshot, JudgmentInspection, SentChoiceQuestion,
            SentScoreQuestion, SentNoulQuestion,
        )
        criteria = {"keep": "継続"}
        question = SentChoiceQuestion("判定", criteria)
        criteria["keep"] = "変更"
        self.assertEqual(question.criteria["keep"], "継続")
        with self.assertRaises(TypeError):
            question.criteria["keep"] = "変更"
        score_criteria = ["低", "中", "高"]
        score_question = SentScoreQuestion("支障", score_criteria)
        score_criteria.clear()
        self.assertEqual(score_question.criteria, ("低", "中", "高"))
        self.assertFalse(hasattr(SentNoulQuestion("急ぎ"), "criteria"))
        check = PolicyCheck("confidence_below_threshold", 0.6999, "gte", 0.7, False)
        reasons = ["confidence_below_threshold"]
        checks = [check]
        decision = NormalizationDecision("needs_review", reasons, checks)
        reasons.clear()
        checks.clear()
        self.assertEqual(decision.reasons, ("confidence_below_threshold",))
        self.assertEqual(decision.checks[0].actual, 0.6999)
        request = JudgmentRequestSerializer(data=self.valid_payload())
        self.assertTrue(request.is_valid())
        context = request.to_request().context
        questions = {"change": question}
        normalization = {"change": decision}
        inspection = JudgmentInspection(
            JudgmentStateSnapshot("入力", context.question, "質問", context.confirmed,
                                  context.impact, ["前の発言"]),
            questions,
            AdoptionPolicySnapshot(ChoiceAdoptionPolicy(0.7, 0.7, True),
                                   ScoreAdoptionPolicy("present", 0.7, 1.5),
                                   NoulAdoptionPolicy(0.8, 0.2)),
            normalization,
        )
        questions.clear()
        normalization.clear()
        self.assertIn("change", inspection.questions)
        self.assertIn("change", inspection.normalization)
        with self.assertRaises(FrozenInstanceError):
            inspection.question_version = "future"

    # テストケース: 不正な版、負数・上限超過のrevision、入れ子の未知キー、相談の種類が未確定の確定回答を渡す。
    # 期待値: 型付き要求へ変換する前に、不正な入力と矛盾する文脈を拒否する。
    def test_rejects_invalid_versions_and_nested_context(self):
        from copy import deepcopy
        base = self.valid_payload()
        cases = []
        for field, value in (("contractVersion", 1), ("contractVersion", 3),
                             ("contractVersion", True), ("revision", -1),
                             ("revision", 2**53), ("revision", "2")):
            payload = deepcopy(base)
            payload[field] = value
            cases.append(payload)
        for key, value in (("topic", "other"), ("scope", "all"),
                           ("workaround", "can_read"), ("urgency", True),
                           ("extra", None)):
            payload = deepcopy(base)
            payload["context"]["confirmed"][key] = value
            cases.append(payload)
        payload = deepcopy(base)
        payload["context"]["extra"] = None
        cases.append(payload)
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertFalse(JudgmentRequestSerializer(data=payload).is_valid())
