from datetime import UTC, datetime, timedelta

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from textjudgmentlab.jev_gateway import JevTransportFailure, JevTransportSuccess
from textjudgmentlab.limits import LabLimitPermit, LabLimits
from textjudgmentlab.services import JudgmentService
from textjudgmentlab.types import JudgmentFailure, JudgmentSuccess, LabPrincipal

from .test_judgment import _request, _valid_answers


class _Gateway:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.payloads: list[object] = []

    async def evaluate(self, payload):
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return self.result


class _Clock:
    def __init__(self, *values: datetime) -> None:
        self.values = iter(values)

    def __call__(self) -> datetime:
        return next(self.values)


class JudgmentServiceTests(SimpleTestCase):
    # テストケース: 有効な本人が判定を一回実行する。
    # 期待値: 利用枠、固定質問、gateway、policyを順に合成して成功を返す。
    def test_composes_single_successful_judgment(self) -> None:
        now = datetime(2026, 9, 21, tzinfo=UTC)
        gateway = _Gateway(
            JevTransportSuccess(
                {"model": "jev-1.13.0", "answers": _valid_answers()}, 25.0
            )
        )
        service = JudgmentService(
            model="jev-1.13.0",
            gateway=gateway,
            limits=LabLimits(),
            clock=_Clock(now, now),
        )
        principal = LabPrincipal(now + timedelta(minutes=1), "owner-a")

        result = async_to_sync(service.evaluate)(principal, _request())

        self.assertIsInstance(result, JudgmentSuccess)
        self.assertEqual(len(gateway.payloads), 1)
        self.assertEqual(tuple(gateway.payloads[0]["questions"]), (
            "topic", "relevance", "change", "scope", "workaround", "result",
            "impact_evidence", "impact", "urgency",
        ))

    # テストケース: Jev応答中に本人principalが失効する。
    # 期待値: 正常な外部結果でも公開せずaccess_expiredを返す。
    def test_discards_result_when_principal_expires_after_remote_call(self) -> None:
        now = datetime(2026, 9, 21, tzinfo=UTC)
        gateway = _Gateway(
            JevTransportSuccess(
                {"model": "jev-1.13.0", "answers": _valid_answers()}, 25.0
            )
        )
        service = JudgmentService(
            model="jev-1.13.0",
            gateway=gateway,
            limits=LabLimits(),
            clock=_Clock(now, now + timedelta(seconds=2)),
        )
        principal = LabPrincipal(now + timedelta(seconds=1), "owner-a")

        result = async_to_sync(service.evaluate)(principal, _request())

        self.assertEqual(result, JudgmentFailure("access_expired"))

    # テストケース: gatewayが想定外例外を送出する。
    # 期待値: 安全な失敗へ縮約し、本人の実行枠を必ず解放する。
    def test_releases_limit_and_hides_unexpected_gateway_error(self) -> None:
        now = datetime(2026, 9, 21, tzinfo=UTC)
        limits = LabLimits()
        service = JudgmentService(
            model="jev-1.13.0",
            gateway=_Gateway(error=RuntimeError("raw-secret-canary")),
            limits=limits,
            clock=lambda: now,
        )
        principal = LabPrincipal(now + timedelta(minutes=1), "owner-a")

        result = async_to_sync(service.evaluate)(principal, _request())

        self.assertEqual(result, JudgmentFailure("unexpected"))
        self.assertNotIn("raw-secret-canary", repr(result))
        self.assertIsInstance(limits.acquire("owner-a"), LabLimitPermit)

    # テストケース: 本人の同時実行枠が使用中である。
    # 期待値: gatewayを呼ばず429相当の安全な失敗を返す。
    def test_rate_limit_stops_before_external_call(self) -> None:
        now = datetime(2026, 9, 21, tzinfo=UTC)
        limits = LabLimits()
        active = limits.acquire("owner-a")
        gateway = _Gateway(JevTransportFailure())
        service = JudgmentService(
            model="jev-1.13.0", gateway=gateway, limits=limits, clock=lambda: now
        )
        principal = LabPrincipal(now + timedelta(minutes=1), "owner-a")

        result = async_to_sync(service.evaluate)(principal, _request())

        self.assertEqual(result, JudgmentFailure("rate_limited"))
        self.assertEqual(gateway.payloads, [])
        assert isinstance(active, LabLimitPermit)
        active.release()

    # テストケース: gatewayが不完全応答をtransport失敗へ縮約する。
    # 期待値: serviceも部分結果を公開せずjudge_unavailableだけを返す。
    def test_transport_failure_never_publishes_partial_result(self) -> None:
        now = datetime(2026, 9, 21, tzinfo=UTC)
        service = JudgmentService(
            model="jev-1.13.0",
            gateway=_Gateway(JevTransportFailure()),
            limits=LabLimits(),
            clock=_Clock(now, now),
        )
        principal = LabPrincipal(now + timedelta(minutes=1), "owner-a")

        result = async_to_sync(service.evaluate)(principal, _request())

        self.assertEqual(result, JudgmentFailure("judge_unavailable"))

    # テストケース: gatewayの全体deadline超過をserviceへ返す。
    # 期待値: 通常のJev障害と混同せずjudge_timeoutを維持する。
    def test_preserves_transport_timeout_classification(self) -> None:
        now = datetime(2026, 9, 21, tzinfo=UTC)
        service = JudgmentService(
            model="jev-1.13.0",
            gateway=_Gateway(JevTransportFailure("judge_timeout")),
            limits=LabLimits(),
            clock=_Clock(now, now),
        )
        principal = LabPrincipal(now + timedelta(minutes=1), "owner-a")

        result = async_to_sync(service.evaluate)(principal, _request())

        self.assertEqual(result, JudgmentFailure("judge_timeout"))


class JudgmentInspectionServiceTests(SimpleTestCase):
    # テストケース: 判定への送信開始から全回答の正規化完了までの時間を測り、成功応答を返す。
    # 期待値: 閲覧用の記録が実際の送信内容と一致する。処理時間には通信だけでなく正規化も含める。
    def test_service_returns_v2_snapshot_and_total_normalization_time(self):
        from unittest.mock import patch
        from textjudgmentlab.judgment_questions import state_payload, questions_payload
        now = datetime(2026, 9, 21, tzinfo=UTC)
        gateway = _Gateway(JevTransportSuccess({"model": "jev-1.13.0", "answers": _valid_answers()}, 25))
        service = JudgmentService(model="jev-1.13.0", gateway=gateway,
            limits=LabLimits(), clock=lambda: now)
        with patch("textjudgmentlab.services.monotonic", side_effect=(10.0, 10.125)):
            result = async_to_sync(service.evaluate)(LabPrincipal(now + timedelta(minutes=1), "owner"), _request())
        self.assertIsInstance(result, JudgmentSuccess)
        self.assertEqual(result.contract_version, 2)
        self.assertEqual(result.details.jev_elapsed_ms, 125)
        self.assertEqual(state_payload(result.inspection.state), gateway.payloads[0]["state"])
        self.assertEqual(questions_payload(result.inspection.questions), gateway.payloads[0]["questions"])
        self.assertEqual(set(result.inspection.normalization), set(gateway.payloads[0]["questions"]))

    # テストケース: 判定結果の正規化中に、本人の認証期限が切れる。
    # 期待値: 正規化後に認証期限を再確認し、期限切れの場合は成功応答を返さない。
    def test_rechecks_principal_after_normalization(self):
        from unittest.mock import patch
        from textjudgmentlab.judgment_policy import normalize_judgment
        now = datetime(2026, 9, 21, tzinfo=UTC)
        current = [now]
        def normalize(*args, **kwargs):
            result = normalize_judgment(*args, **kwargs)
            current[0] += timedelta(seconds=2)
            return result
        service = JudgmentService(model="jev-1.13.0", limits=LabLimits(), clock=lambda: current[0],
            gateway=_Gateway(JevTransportSuccess({"model": "jev-1.13.0", "answers": _valid_answers()}, 25)))
        with patch("textjudgmentlab.services.normalize_judgment", side_effect=normalize):
            result = async_to_sync(service.evaluate)(LabPrincipal(now + timedelta(seconds=1), "owner"), _request())
        self.assertEqual(result, JudgmentFailure("access_expired"))
