from time import perf_counter
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from linewebhooks.models import WebhookEventReceipt
from linewebhooks.tests.support import (
    CHANNEL_ID,
    EVENT_IDS,
    RecordingHandler,
    build_service,
    event,
    signed_payload,
)
from linewebhooks.types import IngressAccepted
from linewebhooks.views import WebhookAPIView


PERFORMANCE_EVENT_IDS = tuple(
    f"01ARZ3NDEKTSV4RRFFQ69G5FA{suffix}" for suffix in "23456789AB"
)
EVENT_QUERY_BUDGETS = {1: 6, 5: 22, 10: 42}
PATH_QUERY_BUDGETS = {"empty": 0, "duplicate": 7, "unsupported": 5}


class _SequenceClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class WebhookPerformanceIntegrationTests(TransactionTestCase):
    reset_sequences = True

    def _post(self, service: object, raw_body: bytes, signature: str):
        with patch.object(WebhookAPIView, "service_factory", return_value=service):
            return self.client.post(
                f"/api/line/webhooks/{CHANNEL_ID}/",
                data=raw_body,
                content_type="application/json",
                HTTP_X_LINE_SIGNATURE=signature,
            )

    # テストケース: 1件・5件・10件の署名済みrequestを標準Backend/MySQL経路で測定する
    # 期待値: 各requestが内部目標1,500msと外部契約2,000ms未満で、query数はevent数に対して線形となる
    def test_one_five_and_ten_event_requests_meet_latency_and_query_budgets(
        self,
    ) -> None:
        query_counts: dict[int, int] = {}
        for event_count in (1, 5, 10):
            with self.subTest(event_count=event_count):
                WebhookEventReceipt.objects.all().delete()
                handler = RecordingHandler()
                service, _ = build_service(handler=handler)
                raw_body, signature = signed_payload(
                    [event(item) for item in PERFORMANCE_EVENT_IDS[:event_count]]
                )

                started_at = perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    response = self._post(service, raw_body, signature)
                elapsed_ms = (perf_counter() - started_at) * 1000

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"")
                self.assertLess(elapsed_ms, 1500)
                self.assertLess(elapsed_ms, 2000)
                self.assertEqual(len(handler.events), event_count)
                self.assertEqual(WebhookEventReceipt.objects.count(), event_count)
                query_counts[event_count] = len(queries)
                self.assertLessEqual(
                    len(queries), EVENT_QUERY_BUDGETS[event_count]
                )

        self.assertEqual(query_counts[5] - query_counts[1], 4 * (5 - 1))
        self.assertEqual(query_counts[10] - query_counts[5], 4 * (10 - 5))

    # テストケース: empty・duplicate・unsupported pathを標準Backend/MySQL経路で測定する
    # 期待値: 全pathが2,000ms未満の空200で、emptyはqueryなし、duplicateとunsupportedはhandlerを呼ばない
    def test_empty_duplicate_and_unsupported_paths_are_fast_and_minimal(self) -> None:
        handler = RecordingHandler()
        service, _ = build_service(handler=handler)
        known_body, known_signature = signed_payload([event(EVENT_IDS[0])])
        first = self._post(service, known_body, known_signature)
        self.assertEqual(first.status_code, 200)
        handler.events.clear()
        cases = (
            ("empty", *signed_payload([])),
            ("duplicate", known_body, known_signature),
        )
        for name, raw_body, signature in cases:
            with self.subTest(path=name):
                started_at = perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    response = self._post(service, raw_body, signature)
                elapsed_ms = (perf_counter() - started_at) * 1000
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"")
                self.assertLess(elapsed_ms, 2000)
                self.assertLessEqual(len(queries), PATH_QUERY_BUDGETS[name])

        unsupported_service, _ = build_service()
        unsupported_body, unsupported_signature = signed_payload(
            [event(EVENT_IDS[1], event_type="future-event")]
        )
        started_at = perf_counter()
        with CaptureQueriesContext(connection) as unsupported_queries:
            unsupported = self._post(
                unsupported_service, unsupported_body, unsupported_signature
            )
        unsupported_elapsed_ms = (perf_counter() - started_at) * 1000

        self.assertEqual(unsupported.status_code, 200)
        self.assertEqual(unsupported.content, b"")
        self.assertLess(unsupported_elapsed_ms, 2000)
        self.assertLessEqual(
            len(unsupported_queries), PATH_QUERY_BUDGETS["unsupported"]
        )
        self.assertEqual(handler.events, [])

    # テストケース: handler callback内でDB transaction状態を観測する
    # 期待値: receipt受付transactionがcommitされた後だけhandlerが呼ばれ、処理時間をtransactionへ含めない
    def test_handler_runs_outside_receipt_transaction(self) -> None:
        atomic_states: list[bool] = []
        handler = RecordingHandler(
            callback=lambda event: atomic_states.append(connection.in_atomic_block)
        )
        service, _ = build_service(handler=handler)
        raw_body, signature = signed_payload(
            [event(EVENT_IDS[0]), event(EVENT_IDS[1])]
        )

        response = self._post(service, raw_body, signature)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(atomic_states, [False, False])

    # テストケース: monotonic clock上で受付処理を2,000ms以上経過させる
    # 期待値: request結果を変えず、非負elapsed millisecondsだけを持つdeadline監査を残す
    def test_deadline_overrun_records_content_free_elapsed_audit(self) -> None:
        service, audit = build_service(
            monotonic_clock=_SequenceClock(10.0, 12.001)
        )
        raw_body, signature = signed_payload([])

        result = service.ingest(str(CHANNEL_ID), raw_body, signature)

        self.assertIsInstance(result, IngressAccepted)
        deadline = audit.entries[-1]
        self.assertEqual(deadline.outcome, "response_deadline_exceeded")
        self.assertEqual(deadline.elapsed_ms, 2000)
        self.assertIsNone(deadline.webhook_event_id)
        self.assertIsNone(deadline.event_type)
