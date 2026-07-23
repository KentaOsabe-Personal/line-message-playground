from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch
from uuid import UUID

from django.db import DatabaseError
from django.test import TestCase as DjangoTestCase

from linechannels.types import (
    ChannelSecret,
    CredentialUnavailable,
    WebhookChannelAvailable,
)
from linewebhooks.services import WebhookIngressService
from linewebhooks.types import (
    HandlerFailed,
    HandlerExecutionContext,
    HandlerRegistration,
    HandlerSucceeded,
    IngressAccepted,
    IngressRejected,
    PayloadRejected,
    ReceiptDecision,
    ReceiptStorageFailed,
    VerifiedEventData,
    VerifiedWebhookEvent,
    VerifiedWebhookPayload,
    WebhookAuditEntry,
)
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.repositories import DjangoEventReceiptRepository
from linewebhooks.tests.support import (
    CHANNEL_ID as INTEGRATION_CHANNEL_ID,
    EVENT_IDS,
    RecordingHandler,
    build_service,
    event as integration_event,
    sign_raw_body,
    signed_payload,
)


CHANNEL_ID = UUID("12345678-1234-4234-9234-123456789abc")
EVENT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class _CredentialRepository:
    def __init__(self, result: object, trace: list[str]) -> None:
        self.result = result
        self.trace = trace

    def get(self, channel_public_id: UUID) -> object:
        self.trace.append("credential")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _SignatureVerifier:
    def __init__(self, result: str, trace: list[str]) -> None:
        self.result = result
        self.trace = trace

    def verify(self, raw_body: bytes, signature: str | None, secret: object) -> str:
        self.trace.append("signature")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _PayloadValidator:
    def __init__(self, result: object, trace: list[str]) -> None:
        self.result = result
        self.trace = trace

    def validate(self, raw_body: bytes, expected_bot_user_id: str) -> object:
        self.trace.append("payload")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _ReceiptRepository:
    def __init__(self, result: object, trace: list[str]) -> None:
        self.result = result
        self.trace = trace
        self.candidates: tuple[object, ...] = ()
        self.finalizations: list[tuple[str, int]] = []

    def accept_batch(self, candidates: tuple[object, ...]) -> object:
        self.trace.append("accept")
        self.candidates = candidates
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def mark_processed(self, receipt_id: int) -> str:
        self.trace.append("finalize_processed")
        self.finalizations.append(("processed", receipt_id))
        return "updated"

    def mark_failed(self, receipt_id: int, code: str) -> str:
        self.trace.append("finalize_failed")
        self.finalizations.append((code, receipt_id))
        return "updated"


class _Handler:
    def __init__(self, result: object, trace: list[str]) -> None:
        self.result = result
        self.trace = trace
        self.events: list[object] = []

    def handle(
        self,
        event: object,
        context: HandlerExecutionContext,
    ) -> object:
        self.trace.append("handle")
        self.events.append(event)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Registry:
    def __init__(self, handlers: dict[str, object], trace: list[str]) -> None:
        self.handlers = handlers
        self.trace = trace

    def resolve(self, event_type: str) -> object | None:
        self.trace.append(f"resolve:{event_type}")
        handler = self.handlers.get(event_type)
        if handler is None:
            return None
        return HandlerRegistration(event_type, handler, "local")


class _Audit:
    def __init__(self) -> None:
        self.entries: list[WebhookAuditEntry] = []

    def record(self, entry: WebhookAuditEntry) -> None:
        self.entries.append(entry)


class _MonotonicClock:
    def __init__(self, values: list[float]) -> None:
        self.values = iter(values)
        self.last = values[-1]

    def __call__(self) -> float:
        return next(self.values, self.last)


def _event(
    webhook_event_id: str = EVENT_ID,
    event_type: str = "message",
) -> VerifiedEventData:
    from linewebhooks.types import FrozenJsonObject

    return VerifiedEventData(
        webhook_event_id=webhook_event_id,
        event_type=event_type,
        occurred_at_ms=100,
        is_redelivery=False,
        event=FrozenJsonObject(
            {
                "webhookEventId": webhook_event_id,
                "type": event_type,
                "timestamp": 100,
                "deliveryContext": {"isRedelivery": False},
            }
        ),
    )


class WebhookIngressServiceTests(TestCase):
    def _service(
        self,
        *,
        signature_result: str = "verified",
        payload: object | None = None,
        receipt_result: object | None = None,
        handlers: dict[str, object] | None = None,
        monotonic_values: list[float] | None = None,
        credential_result: object | None = None,
    ) -> tuple[WebhookIngressService, list[str], _ReceiptRepository, _Audit]:
        trace: list[str] = []
        credential = (
            credential_result
            if credential_result is not None
            else WebhookChannelAvailable(
                channel_public_id=CHANNEL_ID,
                bot_user_id="Ubot",
                channel_secret=ChannelSecret("channel-secret"),
            )
        )
        if payload is None:
            payload = VerifiedWebhookPayload(events=())
        if receipt_result is None:
            receipt_result = ()
        repository = _ReceiptRepository(receipt_result, trace)
        audit = _Audit()
        service = WebhookIngressService(
            credential_repository=_CredentialRepository(credential, trace),
            signature_verifier=_SignatureVerifier(signature_result, trace),
            payload_validator=_PayloadValidator(payload, trace),
            receipt_repository=repository,
            registry=_Registry(handlers or {}, trace),
            audit_logger=audit,
            monotonic_clock=_MonotonicClock(monotonic_values or [1.0, 1.1]),
            observed_at_clock=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        return service, trace, repository, audit

    # テストケース: canonical UUID の有効チャネルで署名済みの空 events を受け付ける
    # 期待値: credential、署名、payload の順に検証し、台帳と handler を使わず accepted になる
    def test_accepts_empty_events_after_ordered_trust_transition(self) -> None:
        service, trace, repository, audit = self._service()

        result = service.ingest(str(CHANNEL_ID), b"{}", "signature")

        self.assertIsInstance(result, IngressAccepted)
        self.assertEqual(trace, ["credential", "signature", "payload"])
        self.assertEqual(repository.candidates, ())
        self.assertEqual([entry.outcome for entry in audit.entries], ["empty_accepted"])

    # テストケース: 資格情報利用不可と各下位依存の予期しない例外を受け取る
    # 期待値: 後続のreceiptとhandlerを呼ばず、内容非露出の安全なrequest結果へ収束する
    def test_credential_unavailable_and_dependency_exceptions_fail_closed(self) -> None:
        service, trace, _, audit = self._service(
            credential_result=CredentialUnavailable("credential_unreadable")
        )
        unavailable = service.ingest(str(CHANNEL_ID), b"body-canary", "signature")
        self.assertEqual(unavailable, IngressRejected(code="channel_unavailable"))
        self.assertEqual(trace, ["credential"])
        self.assertEqual([entry.outcome for entry in audit.entries], ["channel_rejected"])

        cases = (
            (
                {"credential_result": RuntimeError("credential-canary")},
                ["credential"],
            ),
            (
                {"signature_result": RuntimeError("signature-canary")},
                ["credential", "signature"],
            ),
            (
                {"payload": RuntimeError("payload-canary")},
                ["credential", "signature", "payload"],
            ),
            (
                {
                    "payload": VerifiedWebhookPayload((_event(),)),
                    "receipt_result": RuntimeError("storage-canary"),
                },
                [
                    "credential",
                    "signature",
                    "payload",
                    "resolve:message",
                    "accept",
                ],
            ),
        )
        for options, expected_trace in cases:
            with self.subTest(options=options):
                service, trace, _, _ = self._service(**options)  # type: ignore[arg-type]
                result = service.ingest(
                    str(CHANNEL_ID),
                    b"body-canary",
                    "signature",
                )
                self.assertEqual(result, IngressRejected(code="unexpected"))
                self.assertEqual(trace, expected_trace)
    # テストケース: 不正な公開識別子、署名、payload を順番に拒否する
    # 期待値: 各信頼段階より後ろの依存を呼ばず、安全な分類へ収束する
    def test_rejections_stop_before_later_trust_stages(self) -> None:
        service, trace, _, _ = self._service()
        malformed = service.ingest("NOT-A-UUID", b"canary", "signature")
        self.assertEqual(malformed, IngressRejected(code="channel_unavailable"))
        self.assertEqual(trace, [])

        service, trace, _, _ = self._service(signature_result="rejected")
        bad_signature = service.ingest(str(CHANNEL_ID), b"canary", "signature")
        self.assertEqual(bad_signature, IngressRejected(code="signature_rejected"))
        self.assertEqual(trace, ["credential", "signature"])

        service, trace, _, _ = self._service(payload=PayloadRejected())
        bad_payload = service.ingest(str(CHANNEL_ID), b"canary", "signature")
        self.assertEqual(bad_payload, IngressRejected(code="payload_rejected"))
        self.assertEqual(trace, ["credential", "signature", "payload"])

    # テストケース: 既知・未知・重複 event の batch を受け付ける
    # 期待値: support 判定後に一括受付し、新規 processing だけを payload 順に dispatch する
    def test_dispatches_only_new_supported_events_after_batch_commit(self) -> None:
        known = _event()
        unknown = _event("01ARZ3NDEKTSV4RRFFQ69G5FAW", "future")
        duplicate = _event("01ARZ3NDEKTSV4RRFFQ69G5FAX")
        handler = _Handler(HandlerSucceeded(), [])
        decisions = (
            ReceiptDecision(1, known.webhook_event_id, "processing", True),
            ReceiptDecision(2, unknown.webhook_event_id, "unsupported", True),
            ReceiptDecision(3, duplicate.webhook_event_id, "processed", False),
        )
        service, trace, repository, audit = self._service(
            payload=VerifiedWebhookPayload((known, unknown, duplicate)),
            receipt_result=decisions,
            handlers={"message": handler},
        )
        handler.trace = trace

        result = service.ingest(str(CHANNEL_ID), b"{}", "signature")

        self.assertIsInstance(result, IngressAccepted)
        self.assertEqual(
            trace,
            [
                "credential",
                "signature",
                "payload",
                "resolve:message",
                "resolve:future",
                "resolve:message",
                "accept",
                "handle",
                "finalize_processed",
            ],
        )
        self.assertEqual(
            [candidate.initial_status for candidate in repository.candidates],
            ["processing", "unsupported", "processing"],
        )
        self.assertEqual(len(handler.events), 1)
        envelope = handler.events[0]
        self.assertIsInstance(envelope, VerifiedWebhookEvent)
        self.assertEqual(envelope.channel_public_id, CHANNEL_ID)
        self.assertEqual(envelope.webhook_event_id, known.webhook_event_id)
        self.assertEqual(envelope.event_type, known.event_type)
        self.assertEqual(envelope.occurred_at_ms, known.occurred_at_ms)
        self.assertEqual(envelope.is_redelivery, known.is_redelivery)
        self.assertIs(envelope.data, known.event)
        self.assertEqual(repository.finalizations, [("processed", 1)])
        self.assertEqual(
            [entry.outcome for entry in audit.entries],
            [
                "event_accepted",
                "handler_processed",
                "event_unsupported",
                "event_duplicate",
            ],
        )

    # テストケース: handler の安全な失敗と生例外の後にも後続 event を処理する
    # 期待値: 失敗を handler_failed へ確定し、request 全体は accepted になる
    def test_handler_failures_are_safe_and_do_not_stop_later_events(self) -> None:
        first = _event()
        second = _event("01ARZ3NDEKTSV4RRFFQ69G5FAW", "follow")
        failed = _Handler(HandlerFailed(), [])
        exploded = _Handler(RuntimeError("raw-exception-canary"), [])
        decisions = (
            ReceiptDecision(1, first.webhook_event_id, "processing", True),
            ReceiptDecision(2, second.webhook_event_id, "processing", True),
        )
        service, trace, repository, audit = self._service(
            payload=VerifiedWebhookPayload((first, second)),
            receipt_result=decisions,
            handlers={"message": failed, "follow": exploded},
        )
        failed.trace = trace
        exploded.trace = trace

        result = service.ingest(str(CHANNEL_ID), b"{}", "signature")

        self.assertIsInstance(result, IngressAccepted)
        self.assertEqual(
            repository.finalizations,
            [("handler_failed", 1), ("handler_failed", 2)],
        )
        self.assertEqual(
            [entry.outcome for entry in audit.entries].count("handler_failed"),
            2,
        )

    # テストケース: batch 受付または結果確定の保存が失敗する
    # 期待値: batch 失敗では dispatch せず、確定失敗でも後続を処理して storage unavailable を返す
    def test_storage_failures_return_unavailable_without_partial_dispatch(self) -> None:
        event = _event()
        handler = _Handler(HandlerSucceeded(), [])
        service, trace, _, _ = self._service(
            payload=VerifiedWebhookPayload((event,)),
            receipt_result=ReceiptStorageFailed(),
            handlers={"message": handler},
        )
        handler.trace = trace
        result = service.ingest(str(CHANNEL_ID), b"{}", "signature")
        self.assertEqual(result, IngressRejected(code="storage_unavailable"))
        self.assertNotIn("handle", trace)

        decisions = (ReceiptDecision(1, event.webhook_event_id, "processing", True),)
        service, _, repository, _ = self._service(
            payload=VerifiedWebhookPayload((event,)),
            receipt_result=decisions,
            handlers={"message": handler},
        )
        repository.mark_processed = lambda receipt_id: "failed"  # type: ignore[method-assign]
        result = service.ingest(str(CHANNEL_ID), b"{}", "signature")
        self.assertEqual(result, IngressRejected(code="storage_unavailable"))

    # テストケース: 一件目のhandler結果確定に失敗する二件batchを処理する
    # 期待値: storage unavailableを返しつつ二件目もdispatchして結果確定する
    def test_finalize_failure_does_not_stop_later_events(self) -> None:
        first = _event()
        second = _event("01ARZ3NDEKTSV4RRFFQ69G5FAW")
        handler = _Handler(HandlerSucceeded(), [])
        decisions = (
            ReceiptDecision(1, first.webhook_event_id, "processing", True),
            ReceiptDecision(2, second.webhook_event_id, "processing", True),
        )
        service, trace, repository, audit = self._service(
            payload=VerifiedWebhookPayload((first, second)),
            receipt_result=decisions,
            handlers={"message": handler},
        )
        handler.trace = trace

        def finalize(receipt_id: int) -> str:
            repository.finalizations.append(("processed", receipt_id))
            return "failed" if receipt_id == 1 else "updated"

        repository.mark_processed = finalize  # type: ignore[method-assign]

        result = service.ingest(str(CHANNEL_ID), b"{}", "signature")

        self.assertEqual(result, IngressRejected(code="storage_unavailable"))
        self.assertEqual(len(handler.events), 2)
        self.assertEqual(
            repository.finalizations,
            [("processed", 1), ("processed", 2)],
        )
        self.assertEqual(
            [entry.outcome for entry in audit.entries],
            [
                "event_accepted",
                "handler_processed",
                "storage_unavailable",
                "event_accepted",
                "handler_processed",
            ],
        )

    # テストケース: request 全体の単調時刻が2秒以上経過する
    # 期待値: accepted 結果に加えて elapsed milliseconds 付き deadline 監査を残す
    def test_records_deadline_audit_at_two_seconds(self) -> None:
        service, _, _, audit = self._service(monotonic_values=[3.0, 5.0])

        result = service.ingest(str(CHANNEL_ID), b"{}", "signature")

        self.assertIsInstance(result, IngressAccepted)
        self.assertEqual(audit.entries[-1].outcome, "response_deadline_exceeded")
        self.assertEqual(audit.entries[-1].elapsed_ms, 2000)

    # テストケース: request全体の単調時刻が内部目標または2秒未満で完了する
    # 期待値: 1,500msと1,999msではdeadline監査を記録しない
    def test_does_not_record_deadline_audit_below_two_seconds(self) -> None:
        for elapsed in (1.5, 1.999):
            with self.subTest(elapsed=elapsed):
                service, _, _, audit = self._service(
                    monotonic_values=[10.0, 10.0 + elapsed]
                )
                result = service.ingest(str(CHANNEL_ID), b"{}", "signature")
                self.assertIsInstance(result, IngressAccepted)
                self.assertNotIn(
                    "response_deadline_exceeded",
                    [entry.outcome for entry in audit.entries],
                )


class WebhookIngressServiceIntegrationTests(DjangoTestCase):
    # テストケース: 空・既知・未知・複数eventを具象検証器と台帳repositoryで受け付ける
    # 期待値: 新規既知eventだけが順番にdispatchされ、未知eventはunsupported、空eventは台帳なしとなる
    def test_classifies_empty_known_unknown_and_multiple_events(self) -> None:
        handler = RecordingHandler()
        service, audit = build_service(handler=handler)

        empty_body, empty_signature = signed_payload([])
        empty_result = service.ingest(
            str(INTEGRATION_CHANNEL_ID), empty_body, empty_signature
        )
        events = [
            integration_event(EVENT_IDS[0]),
            integration_event(EVENT_IDS[1], event_type="future-event"),
            integration_event(EVENT_IDS[2]),
        ]
        raw_body, signature = signed_payload(events)
        batch_result = service.ingest(
            str(INTEGRATION_CHANNEL_ID), raw_body, signature
        )

        self.assertIsInstance(empty_result, IngressAccepted)
        self.assertIsInstance(batch_result, IngressAccepted)
        self.assertEqual(
            list(
                WebhookEventReceipt.objects.order_by("webhook_event_id").values_list(
                    "webhook_event_id", "status"
                )
            ),
            sorted(
                [
                    (EVENT_IDS[0], "processed"),
                    (EVENT_IDS[1], "unsupported"),
                    (EVENT_IDS[2], "processed"),
                ]
            ),
        )
        self.assertEqual(
            [item.webhook_event_id for item in handler.events],
            [EVENT_IDS[0], EVENT_IDS[2]],
        )
        self.assertIn("empty_accepted", [entry.outcome for entry in audit.entries])
        self.assertIn("event_unsupported", [entry.outcome for entry in audit.entries])

    # テストケース: 成功済みeventを異なるmetadataの再送として受け付ける
    # 期待値: handlerは一回だけ呼ばれ、初回metadataとprocessed状態を維持してduplicate監査を残す
    def test_duplicate_preserves_receipt_and_does_not_redispatch(self) -> None:
        handler = RecordingHandler()
        service, audit = build_service(handler=handler)
        first_body, first_signature = signed_payload(
            [integration_event(EVENT_IDS[0], occurred_at_ms=100)]
        )
        duplicate_body, duplicate_signature = signed_payload(
            [
                integration_event(
                    EVENT_IDS[0],
                    occurred_at_ms=999,
                    is_redelivery=True,
                )
            ]
        )

        first = service.ingest(
            str(INTEGRATION_CHANNEL_ID), first_body, first_signature
        )
        duplicate = service.ingest(
            str(INTEGRATION_CHANNEL_ID), duplicate_body, duplicate_signature
        )

        self.assertIsInstance(first, IngressAccepted)
        self.assertIsInstance(duplicate, IngressAccepted)
        receipt = WebhookEventReceipt.objects.get(webhook_event_id=EVENT_IDS[0])
        self.assertEqual(receipt.status, "processed")
        self.assertEqual(receipt.occurred_at_ms, 100)
        self.assertFalse(receipt.is_redelivery)
        self.assertEqual(len(handler.events), 1)
        self.assertEqual(
            [entry.outcome for entry in audit.entries].count("event_duplicate"), 1
        )

    # テストケース: handlerの安全な失敗と生例外を別eventで処理する
    # 期待値: 両方をhandler_failedへ確定し、生例外でbatch分類を中断せずacceptedを返す
    def test_safe_failure_and_exception_finalize_as_failed(self) -> None:
        for result in (HandlerFailed(), RuntimeError("handler-exception-canary")):
            with self.subTest(result=type(result).__name__):
                WebhookEventReceipt.objects.all().delete()
                handler = RecordingHandler(result)
                service, audit = build_service(handler=handler)
                raw_body, signature = signed_payload(
                    [integration_event(EVENT_IDS[0])]
                )

                response = service.ingest(
                    str(INTEGRATION_CHANNEL_ID), raw_body, signature
                )

                self.assertIsInstance(response, IngressAccepted)
                receipt = WebhookEventReceipt.objects.get()
                self.assertEqual(receipt.status, "failed")
                self.assertEqual(receipt.failure_code, "handler_failed")
                self.assertEqual(len(handler.events), 1)
                self.assertIn(
                    "handler_failed", [entry.outcome for entry in audit.entries]
                )

    # テストケース: 一件目が安全なhandler失敗となる二件batchを具象台帳で処理する
    # 期待値: 一件目をfailedへ確定した後も二件目をdispatchし、processedへ確定してacceptedを返す
    def test_safe_handler_failure_does_not_stop_later_integrated_event(self) -> None:
        class SequenceHandler:
            def __init__(self) -> None:
                self.events: list[object] = []

            def handle(
                self,
                event: object,
                context: HandlerExecutionContext,
            ) -> object:
                self.events.append(event)
                return HandlerFailed() if len(self.events) == 1 else HandlerSucceeded()

        handler = SequenceHandler()
        service, audit = build_service(handler=handler)  # type: ignore[arg-type]
        raw_body, signature = signed_payload(
            [integration_event(EVENT_IDS[0]), integration_event(EVENT_IDS[1])]
        )

        result = service.ingest(str(INTEGRATION_CHANNEL_ID), raw_body, signature)

        self.assertIsInstance(result, IngressAccepted)
        self.assertEqual(len(handler.events), 2)
        self.assertEqual(
            list(
                WebhookEventReceipt.objects.order_by("webhook_event_id").values_list(
                    "status", flat=True
                )
            ),
            ["failed", "processed"],
        )
        outcomes = [entry.outcome for entry in audit.entries]
        self.assertEqual(outcomes.count("handler_failed"), 1)
        self.assertEqual(outcomes.count("handler_processed"), 1)

    # テストケース: batch受付保存と一件目の結果確定保存を失敗させる
    # 期待値: 受付失敗は全件dispatchせず、確定失敗後も後続eventを処理してstorage unavailableを返す
    def test_storage_failures_reject_without_partial_acceptance_or_stopping_batch(self) -> None:
        handler = RecordingHandler()
        repository = DjangoEventReceiptRepository()
        service, _ = build_service(
            handler=handler,
            receipt_repository=repository,
        )
        raw_body, signature = signed_payload(
            [integration_event(EVENT_IDS[0]), integration_event(EVENT_IDS[1])]
        )

        with patch.object(
            repository,
            "_create_receipt",
            side_effect=DatabaseError("accept-storage-canary"),
        ):
            rejected = service.ingest(
                str(INTEGRATION_CHANNEL_ID), raw_body, signature
            )

        self.assertEqual(rejected, IngressRejected(code="storage_unavailable"))
        self.assertEqual(WebhookEventReceipt.objects.count(), 0)
        self.assertEqual(handler.events, [])

        original_finalize = repository._conditional_update

        def fail_first_finalize(receipt_id: int, **kwargs: object) -> int:
            if receipt_id == WebhookEventReceipt.objects.order_by("pk").first().pk:
                raise DatabaseError("finalize-storage-canary")
            return original_finalize(receipt_id, **kwargs)  # type: ignore[arg-type]

        with patch.object(
            repository,
            "_conditional_update",
            side_effect=fail_first_finalize,
        ):
            unavailable = service.ingest(
                str(INTEGRATION_CHANNEL_ID), raw_body, signature
            )

        self.assertEqual(
            unavailable, IngressRejected(code="storage_unavailable")
        )
        self.assertEqual(len(handler.events), 2)
        self.assertEqual(
            list(WebhookEventReceipt.objects.order_by("pk").values_list("status", flat=True)),
            ["processing", "processed"],
        )

    # テストケース: channel・署名・destination・payload・上限の各失敗を具象検証器へ渡す
    # 期待値: 全件が安全に拒否され、receiptとhandlerは一件も作成されない
    def test_rejection_branches_never_accept_or_dispatch_events(self) -> None:
        handler = RecordingHandler()
        service, _ = build_service(handler=handler)
        valid_event = integration_event(EVENT_IDS[0])
        valid_body, valid_signature = signed_payload([valid_event])
        wrong_destination, wrong_destination_signature = signed_payload(
            [valid_event], destination="U" + "9" * 32
        )
        over_limit, over_limit_signature = signed_payload(
            [integration_event(EVENT_IDS[0]) for _ in range(11)]
        )
        malformed_json = b'{"destination":'
        cases = (
            ("not-a-uuid", valid_body, valid_signature, "channel_unavailable"),
            (str(INTEGRATION_CHANNEL_ID), valid_body, "bad", "signature_rejected"),
            (
                str(INTEGRATION_CHANNEL_ID),
                wrong_destination,
                wrong_destination_signature,
                "payload_rejected",
            ),
            (
                str(INTEGRATION_CHANNEL_ID),
                malformed_json,
                sign_raw_body(malformed_json),
                "payload_rejected",
            ),
            (
                str(INTEGRATION_CHANNEL_ID),
                over_limit,
                over_limit_signature,
                "payload_rejected",
            ),
        )

        for channel_key, body, signature, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                result = service.ingest(channel_key, body, signature)
                self.assertEqual(result, IngressRejected(code=expected_code))

        self.assertEqual(WebhookEventReceipt.objects.count(), 0)
        self.assertEqual(handler.events, [])

    # テストケース: unknown・inactive・incomplete・unreadable資格情報を具象serviceへ返す
    # 期待値: 全分類がchannel unavailableへ収束し、署名・payload・台帳・handlerを呼ばない
    def test_credential_failures_reject_before_verification_or_receipt(self) -> None:
        class UnavailableCredentialRepository:
            def __init__(self, code: str) -> None:
                self.code = code

            def get(self, channel_public_id: object) -> CredentialUnavailable:
                return CredentialUnavailable(self.code)  # type: ignore[arg-type]

        raw_body, signature = signed_payload([integration_event(EVENT_IDS[0])])
        for code in (
            "channel_not_found",
            "channel_inactive",
            "credentials_incomplete",
            "credential_unreadable",
        ):
            with self.subTest(code=code):
                handler = RecordingHandler()
                service, audit = build_service(
                    handler=handler,
                    credential_repository=UnavailableCredentialRepository(code),
                )

                result = service.ingest(
                    str(INTEGRATION_CHANNEL_ID), raw_body, signature
                )

                self.assertEqual(
                    result, IngressRejected(code="channel_unavailable")
                )
                self.assertEqual(handler.events, [])
                self.assertEqual(
                    [entry.outcome for entry in audit.entries],
                    ["channel_rejected"],
                )
        self.assertEqual(WebhookEventReceipt.objects.count(), 0)
