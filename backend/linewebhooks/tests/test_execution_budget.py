from datetime import datetime, timezone
from unittest import TestCase
from uuid import UUID

from django.test import TestCase as DjangoTestCase

from linechannels.types import ChannelSecret, WebhookChannelAvailable
from linewebhooks.handlers import StaticHandlerRegistry
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.repositories import DjangoEventReceiptRepository
from linewebhooks.services import WebhookIngressService
from linewebhooks.types import (
    HandlerExecutionContext,
    HandlerRegistration,
    HandlerSucceeded,
    ReceiptDecision,
    VerifiedEventData,
    VerifiedWebhookPayload,
)


CHANNEL_ID = UUID("12345678-1234-4234-9234-123456789abc")
EVENT_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "01ARZ3NDEKTSV4RRFFQ69G5FAW",
)


class _Clock:
    def __init__(self, values: tuple[float, ...]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class _CredentialRepository:
    def get(self, channel_public_id: UUID) -> WebhookChannelAvailable:
        return WebhookChannelAvailable(
            channel_public_id=channel_public_id,
            bot_user_id="Ubot",
            channel_secret=ChannelSecret("secret"),
        )


class _Verifier:
    def verify(self, raw_body: bytes, signature: str | None, secret: object) -> str:
        return "verified"


class _Validator:
    def __init__(self, payload: VerifiedWebhookPayload) -> None:
        self.payload = payload

    def validate(self, raw_body: bytes, expected_bot_user_id: str) -> object:
        return self.payload


class _ReceiptRepository:
    def __init__(self) -> None:
        self.deadline_failures: list[int] = []

    def accept_batch(
        self, candidates: tuple[object, ...]
    ) -> tuple[ReceiptDecision, ...]:
        return tuple(
            ReceiptDecision(
                receipt_id=index + 1,
                webhook_event_id=event_id,
                status="processing",
                created=True,
            )
            for index, event_id in enumerate(EVENT_IDS)
        )

    def mark_processed(self, receipt_id: int) -> str:
        return "updated"

    def mark_failed(self, receipt_id: int, code: str) -> str:
        if code == "dispatch_deadline_exceeded":
            self.deadline_failures.append(receipt_id)
        return "updated"


class _Audit:
    def __init__(self) -> None:
        self.entries: list[object] = []

    def record(self, entry: object) -> None:
        self.entries.append(entry)


class _Handler:
    def __init__(self) -> None:
        self.contexts: list[HandlerExecutionContext] = []

    def handle(
        self, event: object, context: HandlerExecutionContext
    ) -> HandlerSucceeded:
        self.contexts.append(context)
        return HandlerSucceeded()


def _event(event_id: str) -> VerifiedEventData:
    from linewebhooks.types import FrozenJsonObject

    return VerifiedEventData(
        webhook_event_id=event_id,
        event_type="message",
        occurred_at_ms=100,
        is_redelivery=False,
        event=FrozenJsonObject({}),
    )


class HandlerExecutionContractTests(TestCase):
    # テストケース: local と external profile の finite な実行 context を構築する
    # 期待値: local は cutoff なし、external は response deadline 以下だけを受理する
    def test_context_enforces_finite_profile_specific_deadline_contract(self) -> None:
        local = HandlerExecutionContext(
            response_deadline_monotonic=12.0,
            dispatch_index=0,
            remaining_dispatch_count=1,
            external_io_deadline_monotonic=None,
        )
        external = HandlerExecutionContext(
            response_deadline_monotonic=12.0,
            dispatch_index=1,
            remaining_dispatch_count=0,
            external_io_deadline_monotonic=11.7,
        )

        self.assertIsNone(local.external_io_deadline_monotonic)
        self.assertEqual(external.external_io_deadline_monotonic, 11.7)
        with self.assertRaises(ValueError):
            HandlerExecutionContext(float("inf"), 0, 0, None)
        with self.assertRaises(ValueError):
            HandlerExecutionContext(12.0, 10, 0, None)
        with self.assertRaises(ValueError):
            HandlerExecutionContext(12.0, 0, 0, 12.1)

    # テストケース: profile を伴う immutable registration を registry に登録する
    # 期待値: resolve は handler と profile を保持し、欠落・未知 profile・重複を拒否する
    def test_registry_requires_known_execution_profile(self) -> None:
        handler = _Handler()
        registration = HandlerRegistration("message", handler, "local")
        registry = StaticHandlerRegistry((registration,))

        self.assertIs(registry.resolve("message"), registration)
        with self.assertRaises((TypeError, ValueError)):
            StaticHandlerRegistry((("message", handler),))
        with self.assertRaises(ValueError):
            HandlerRegistration("message", handler, "unknown")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            StaticHandlerRegistry((registration, registration))


class DeadlineDispatchTests(TestCase):
    def _service(
        self,
        *,
        clock_values: tuple[float, ...],
        handler: _Handler,
        repository: _ReceiptRepository,
        audit: _Audit,
        profile: str = "local",
    ) -> WebhookIngressService:
        payload = VerifiedWebhookPayload(tuple(_event(item) for item in EVENT_IDS))
        return WebhookIngressService(
            credential_repository=_CredentialRepository(),
            signature_verifier=_Verifier(),
            payload_validator=_Validator(payload),
            receipt_repository=repository,
            registry=StaticHandlerRegistry(
                (
                    HandlerRegistration(
                        "message",
                        handler,
                        profile,  # type: ignore[arg-type]
                    ),
                )
            ),
            audit_logger=audit,
            monotonic_clock=_Clock(clock_values),
            observed_at_clock=lambda: datetime(2026, 7, 23, tzinfo=timezone.utc),
        )

    # テストケース: 十分な予算で二つの local handler を dispatch する
    # 期待値: 同じ absolute deadline と単調な index・残件数を全 handler へ渡す
    def test_propagates_one_deadline_and_monotonic_dispatch_positions(self) -> None:
        handler = _Handler()
        repository = _ReceiptRepository()
        audit = _Audit()
        service = self._service(
            clock_values=(10.0, 10.0, 10.1, 10.2),
            handler=handler,
            repository=repository,
            audit=audit,
        )

        service.ingest(
            str(CHANNEL_ID),
            b"{}",
            "signature",
            request_started_at_monotonic=10.0,
        )

        self.assertEqual(
            [
                (
                    context.response_deadline_monotonic,
                    context.dispatch_index,
                    context.remaining_dispatch_count,
                )
                for context in handler.contexts
            ],
            [(12.0, 0, 1), (12.0, 1, 0)],
        )

    # テストケース: 最初の handler 開始前に local/finalize/response reserve が不足する
    # 期待値: dispatch を閉じて全 processing receipt を専用 failure にし handler を呼ばない
    def test_closes_dispatch_and_finalizes_unstarted_events_on_budget_shortage(
        self,
    ) -> None:
        handler = _Handler()
        repository = _ReceiptRepository()
        audit = _Audit()
        service = self._service(
            clock_values=(11.58, 11.59),
            handler=handler,
            repository=repository,
            audit=audit,
        )

        service.ingest(
            str(CHANNEL_ID),
            b"{}",
            "signature",
            request_started_at_monotonic=10.0,
        )

        self.assertEqual(handler.contexts, [])
        self.assertEqual(repository.deadline_failures, [1, 2])
        self.assertEqual(
            [entry.outcome for entry in audit.entries],
            ["dispatch_deadline_exceeded", "dispatch_deadline_exceeded"],
        )

    # テストケース: deadline-managed external handler を一件 dispatch する
    # 期待値: local・finalize・response予約を引いた finite cutoff を同じ clock domain で渡す
    def test_external_profile_receives_reserved_io_cutoff(self) -> None:
        handler = _Handler()
        repository = _ReceiptRepository()
        audit = _Audit()
        service = self._service(
            clock_values=(10.1, 10.2, 10.3),
            handler=handler,
            repository=repository,
            audit=audit,
            profile="deadline_managed_external",
        )

        service.ingest(
            str(CHANNEL_ID),
            b"{}",
            "signature",
            request_started_at_monotonic=10.0,
        )

        cutoffs = [
            context.external_io_deadline_monotonic for context in handler.contexts
        ]
        self.assertAlmostEqual(cutoffs[0], 11.56)  # type: ignore[arg-type]
        self.assertAlmostEqual(cutoffs[1], 11.68)  # type: ignore[arg-type]


class DispatchDeadlineReceiptTests(DjangoTestCase):
    # テストケース: 初回 processing receipt を handler 開始前の期限超過へ確定して再受付する
    # 期待値: 専用 failure が terminal に残り、再送は created=false で実行権を得ない
    def test_deadline_failure_is_terminal_and_redelivery_is_not_reacquired(
        self,
    ) -> None:
        from linewebhooks.types import ReceiptCandidate

        repository = DjangoEventReceiptRepository()
        candidate = ReceiptCandidate(
            channel_public_id=CHANNEL_ID,
            webhook_event_id=EVENT_IDS[0],
            event_type="message",
            occurred_at_ms=100,
            is_redelivery=False,
            initial_status="processing",
        )
        first = repository.accept_batch((candidate,))
        assert not hasattr(first, "status")
        decision = first[0]  # type: ignore[index]

        finalized = repository.mark_failed(
            decision.receipt_id,
            "dispatch_deadline_exceeded",
        )
        redelivery = repository.accept_batch((candidate,))
        receipt = WebhookEventReceipt.objects.get(pk=decision.receipt_id)

        self.assertEqual(finalized, "updated")
        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.failure_code, "dispatch_deadline_exceeded")
        self.assertIsNotNone(receipt.completed_at)
        self.assertFalse(redelivery[0].created)  # type: ignore[index]
        self.assertEqual(  # type: ignore[index]
            redelivery[0].status,
            "failed",
        )
