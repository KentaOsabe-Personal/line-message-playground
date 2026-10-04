import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from textjudgmentlab.jev_gateway import JevTransportFailure, JevTransportSuccess
from textjudgmentlab.limits import LabLimits
from textjudgmentlab.services import JudgmentService
from textjudgmentlab.types import JudgmentFailure, JudgmentRequest, LabPrincipal

from .test_http_boundary import RUNTIME

_fixture_path = Path("/test-fixtures/text-judgment-lab-v3.json")
if not _fixture_path.exists():
    _fixture_path = (
        Path(__file__).resolve().parents[3] / "frontend/test/fixtures/text-judgment-lab-v3.json"
    )
FIXTURE = json.loads(_fixture_path.read_text())
NOW = datetime(2026, 10, 4, tzinfo=UTC)


class Gateway:
    def __init__(self, result=None):
        self.result = result or JevTransportSuccess(deepcopy(FIXTURE), 125.4)
        self.calls = []

    async def evaluate(self, payload):
        self.calls.append(payload)
        return self.result


@override_settings(TEXT_JUDGMENT_LAB_RUNTIME=RUNTIME)
class FreeTextJudgmentTests(SimpleTestCase):
    def setUp(self):
        self.gateway = Gateway()
        self.principal = LabPrincipal(datetime(2099, 1, 1, tzinfo=UTC), "a" * 64)
        self.service = JudgmentService(
            model=RUNTIME.model, gateway=self.gateway, limits=LabLimits(), clock=lambda: NOW
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.principal)

    def post(self, body):
        with patch("textjudgmentlab.views.build_judgment_service", return_value=self.service):
            return self.client.post(
                "/api/labs/text-judgment/judgments", body, format="json", HTTP_ORIGIN=RUNTIME.origin
            )

    # テストケース: 任意の文章を送信し、3種類のJev判定を返す。
    # 期待値: 履歴なしで固定3質問を一度だけ送り、低confidenceも補正せず共有契約どおり返す。
    def test_free_text_round_trip_preserves_values_and_sends_only_current_text(self):
        response = self.post({"contractVersion": 3, "text": "  明日までに資料を送ってほしい  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), FIXTURE)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(len(self.gateway.calls), 1)
        payload = self.gateway.calls[0]
        self.assertEqual(payload["state"], "明日までに資料を送ってほしい")
        self.assertEqual(set(payload), {"state", "model", "questions"})
        self.assertEqual(
            {k: v["type"] for k, v in payload["questions"].items()},
            {"intent": "choice", "sentiment": "score", "urgency": "noul"},
        )
        self.post({"contractVersion": 3, "text": "今日はとても嬉しい！"})
        self.assertEqual(self.gateway.calls[1]["state"], "今日はとても嬉しい！")

    # テストケース: 空欄・文字数超過・非文字列・旧契約・余計な履歴を送る。
    # 期待値: 400で拒否し、Jevを呼ばない。1000文字の日本語は受理する。
    def test_rejects_invalid_input_before_calling_jev(self):
        for body in (
            {"contractVersion": 3, "text": " "},
            {"contractVersion": 3, "text": "あ" * 1001},
            {"contractVersion": 3, "text": 3},
            {"contractVersion": 2, "text": "文章"},
            {"contractVersion": True, "text": "文章"},
            {"contractVersion": 3, "text": "文章", "context": {}},
            [],
        ):
            with self.subTest(body=str(body)[:80]):
                response = self.post(body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["error"]["code"], "invalid_input")
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.post({"contractVersion": 3, "text": "あ" * 1000}).status_code, 200)

    # テストケース: Jevの型違い・不正数値・欠損・未知モデル・不整合を返す。
    # 期待値: 不正な値を画面へ渡さず502にする。
    def test_rejects_invalid_jev_answers(self):
        mutations = [
            lambda x: x["answers"]["intent"].update(choice="invented"),
            lambda x: x["answers"]["intent"].update(choice="other"),
            lambda x: x["answers"]["intent"].update(confidence=True),
            lambda x: x["answers"]["intent"]["probabilities"].update(other=0.9),
            lambda x: x["answers"]["sentiment"].update(score=float("nan")),
            lambda x: x["answers"]["sentiment"].update(score=3),
            lambda x: x["answers"]["urgency"].update(noul=-0.1),
            lambda x: x["answers"].pop("urgency"),
            lambda x: x.update(model="unknown"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                raw = deepcopy(FIXTURE)
                mutate(raw)
                self.gateway.result = JevTransportSuccess(raw, 10)
                response = self.post({"contractVersion": 3, "text": "試す"})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.data["error"]["code"], "judgment_failed")

    # テストケース: Jevがタイムアウトまたは通信失敗する。
    # 期待値: 公開エラーだけ返し、自動再試行しない。失敗後に制限permitを解放する。
    def test_failure_does_not_retry_and_releases_limit(self):
        for code, expected in [("judge_timeout", 504), ("judge_unavailable", 502)]:
            self.gateway.result = JevTransportFailure(code)
            response = self.post({"contractVersion": 3, "text": "試す"})
            self.assertEqual(response.status_code, expected)
        self.assertEqual(len(self.gateway.calls), 2)
        self.gateway.result = JevTransportSuccess(deepcopy(FIXTURE), 10)
        self.assertEqual(self.post({"contractVersion": 3, "text": "再度"}).status_code, 200)

    # テストケース: Jev処理前または処理中に認証期限が切れる。
    # 期待値: 期限切れの結果を返さず、処理前失効では外部呼出もしない。
    def test_checks_expiry_before_and_after_jev(self):
        principal = LabPrincipal(NOW + timedelta(seconds=1), "a" * 64)
        times = iter([NOW, NOW + timedelta(seconds=2)])
        service = JudgmentService(
            model=RUNTIME.model, gateway=self.gateway, limits=LabLimits(), clock=lambda: next(times)
        )
        self.assertEqual(
            async_to_sync(service.evaluate)(principal, JudgmentRequest("試す")),
            JudgmentFailure("access_expired"),
        )
        expired = LabPrincipal(NOW, "a" * 64)
        self.assertEqual(
            async_to_sync(self.service.evaluate)(expired, JudgmentRequest("試す")),
            JudgmentFailure("access_expired"),
        )
        self.assertEqual(len(self.gateway.calls), 1)
