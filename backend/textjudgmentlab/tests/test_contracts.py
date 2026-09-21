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
            "contractVersion": 1,
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
