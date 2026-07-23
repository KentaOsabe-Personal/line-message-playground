from collections.abc import Callable
from datetime import datetime
from math import isfinite
from time import monotonic
from uuid import UUID

from django.utils import timezone

from linechannels.types import WebhookChannelAvailable

from .types import (
    HandlerSucceeded,
    HandlerExecutionContext,
    HandlerRegistration,
    IngressAccepted,
    IngressRejected,
    IngressResult,
    PayloadRejected,
    ReceiptCandidate,
    ReceiptDecision,
    ReceiptStorageFailed,
    VerifiedEventData,
    VerifiedWebhookEvent,
    VerifiedWebhookPayload,
    WebhookAuditEntry,
)


_DEADLINE_SECONDS = 2.0
_LOCAL_HANDLER_RESERVE_SECONDS = 0.1
_RECEIPT_FINALIZE_RESERVE_SECONDS = 0.02
_HTTP_RESPONSE_RESERVE_SECONDS = 0.2


class WebhookIngressService:
    def __init__(
        self,
        *,
        credential_repository: object,
        signature_verifier: object,
        payload_validator: object,
        receipt_repository: object,
        registry: object,
        audit_logger: object,
        monotonic_clock: Callable[[], float] = monotonic,
        observed_at_clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._credential_repository = credential_repository
        self._signature_verifier = signature_verifier
        self._payload_validator = payload_validator
        self._receipt_repository = receipt_repository
        self._registry = registry
        self._audit_logger = audit_logger
        self._monotonic_clock = monotonic_clock
        self._observed_at_clock = observed_at_clock

    def ingest(
        self,
        channel_public_key: str,
        raw_body: bytes,
        signature: str | None,
        *,
        request_started_at_monotonic: float | None = None,
    ) -> IngressResult:
        started_at = (
            self._monotonic_clock()
            if request_started_at_monotonic is None
            else request_started_at_monotonic
        )
        if (
            not isinstance(started_at, (int, float))
            or isinstance(started_at, bool)
            or not isfinite(started_at)
            or started_at <= 0
        ):
            return IngressRejected(code="unexpected")
        response_deadline = started_at + _DEADLINE_SECONDS
        channel_public_id: UUID | None = None
        try:
            channel_public_id = self._canonical_uuid4(channel_public_key)
            if channel_public_id is None:
                self._audit("channel_rejected")
                return IngressRejected(code="channel_unavailable")

            credential = self._credential_repository.get(channel_public_id)  # type: ignore[attr-defined]
            if not isinstance(credential, WebhookChannelAvailable):
                self._audit("channel_rejected", channel_public_id=channel_public_id)
                return IngressRejected(code="channel_unavailable")

            signature_result = self._signature_verifier.verify(  # type: ignore[attr-defined]
                raw_body,
                signature,
                credential.channel_secret,
            )
            if signature_result != "verified":
                self._audit("signature_rejected", channel_public_id=channel_public_id)
                return IngressRejected(code="signature_rejected")

            payload = self._payload_validator.validate(  # type: ignore[attr-defined]
                raw_body,
                credential.bot_user_id,
            )
            if isinstance(payload, PayloadRejected) or not isinstance(
                payload, VerifiedWebhookPayload
            ):
                self._audit("payload_rejected", channel_public_id=channel_public_id)
                return IngressRejected(code="payload_rejected")

            if not payload.events:
                self._audit("empty_accepted", channel_public_id=channel_public_id)
                return IngressAccepted()

            registrations = tuple(
                self._registry.resolve(event.event_type)  # type: ignore[attr-defined]
                for event in payload.events
            )
            candidates = tuple(
                self._candidate(
                    channel_public_id,
                    event,
                    registration is not None,
                )
                for event, registration in zip(
                    payload.events,
                    registrations,
                    strict=True,
                )
            )
            decisions = self._receipt_repository.accept_batch(candidates)  # type: ignore[attr-defined]
            if isinstance(decisions, ReceiptStorageFailed) or not self._valid_decisions(
                payload.events, decisions
            ):
                self._audit("storage_unavailable", channel_public_id=channel_public_id)
                return IngressRejected(code="storage_unavailable")

            storage_failed = self._dispatch(
                channel_public_id,
                payload.events,
                registrations,
                decisions,
                response_deadline,
            )
            if storage_failed:
                return IngressRejected(code="storage_unavailable")
            return IngressAccepted()
        except Exception:
            return IngressRejected(code="unexpected")
        finally:
            elapsed_seconds = max(0.0, self._monotonic_clock() - started_at)
            if elapsed_seconds >= _DEADLINE_SECONDS:
                self._audit(
                    "response_deadline_exceeded",
                    channel_public_id=channel_public_id,
                    elapsed_ms=int(elapsed_seconds * 1000),
                )

    @staticmethod
    def _canonical_uuid4(value: str) -> UUID | None:
        try:
            parsed = UUID(value)
        except (AttributeError, TypeError, ValueError):
            return None
        if parsed.version != 4 or str(parsed) != value:
            return None
        return parsed

    @staticmethod
    def _candidate(
        channel_public_id: UUID,
        event: VerifiedEventData,
        supported: bool,
    ) -> ReceiptCandidate:
        return ReceiptCandidate(
            channel_public_id=channel_public_id,
            webhook_event_id=event.webhook_event_id,
            event_type=event.event_type,
            occurred_at_ms=event.occurred_at_ms,
            is_redelivery=event.is_redelivery,
            initial_status="processing" if supported else "unsupported",
        )

    @staticmethod
    def _valid_decisions(
        events: tuple[VerifiedEventData, ...],
        decisions: object,
    ) -> bool:
        return (
            isinstance(decisions, tuple)
            and len(events) == len(decisions)
            and all(isinstance(decision, ReceiptDecision) for decision in decisions)
            and all(
                event.webhook_event_id == decision.webhook_event_id
                for event, decision in zip(events, decisions, strict=True)
            )
        )

    def _dispatch(
        self,
        channel_public_id: UUID,
        events: tuple[VerifiedEventData, ...],
        registrations: tuple[object | None, ...],
        decisions: tuple[ReceiptDecision, ...],
        response_deadline: float,
    ) -> bool:
        storage_failed = False
        dispatch_closed = False
        dispatch_index = 0
        dispatchable = tuple(
            isinstance(registration, HandlerRegistration)
            and decision.created
            and decision.status == "processing"
            for registration, decision in zip(
                registrations,
                decisions,
                strict=True,
            )
        )
        for position, (event, registration, decision) in enumerate(
            zip(events, registrations, decisions, strict=True)
        ):
            if not decision.created:
                self._audit_event("event_duplicate", channel_public_id, event)
                continue
            if decision.status == "unsupported" or registration is None:
                self._audit_event("event_unsupported", channel_public_id, event)
                continue
            if decision.status != "processing":
                self._audit_event("event_duplicate", channel_public_id, event)
                continue
            if not isinstance(registration, HandlerRegistration):
                storage_failed = True
                self._audit_event("storage_unavailable", channel_public_id, event)
                continue

            remaining_dispatch_count = sum(dispatchable[position + 1 :])
            pending_dispatch_count = remaining_dispatch_count + 1
            if not dispatch_closed:
                required_reserve = (
                    pending_dispatch_count * _LOCAL_HANDLER_RESERVE_SECONDS
                    + pending_dispatch_count
                    * _RECEIPT_FINALIZE_RESERVE_SECONDS
                    + _HTTP_RESPONSE_RESERVE_SECONDS
                )
                dispatch_closed = (
                    self._monotonic_clock() + required_reserve
                    > response_deadline
                )
            if dispatch_closed:
                finalization = self._receipt_repository.mark_failed(  # type: ignore[attr-defined]
                    decision.receipt_id,
                    "dispatch_deadline_exceeded",
                )
                self._audit_event(
                    "dispatch_deadline_exceeded",
                    channel_public_id,
                    event,
                )
                if finalization == "failed":
                    storage_failed = True
                    self._audit_event(
                        "storage_unavailable",
                        channel_public_id,
                        event,
                    )
                continue

            self._audit_event("event_accepted", channel_public_id, event)
            external_cutoff = None
            if registration.execution_profile == "deadline_managed_external":
                external_cutoff = response_deadline - (
                    pending_dispatch_count * _LOCAL_HANDLER_RESERVE_SECONDS
                    + pending_dispatch_count
                    * _RECEIPT_FINALIZE_RESERVE_SECONDS
                    + _HTTP_RESPONSE_RESERVE_SECONDS
                )
            context = HandlerExecutionContext(
                response_deadline_monotonic=response_deadline,
                dispatch_index=dispatch_index,
                remaining_dispatch_count=remaining_dispatch_count,
                external_io_deadline_monotonic=external_cutoff,
            )
            try:
                outcome = registration.handler.handle(  # type: ignore[attr-defined]
                    self._envelope(channel_public_id, event),
                    context,
                )
            except Exception:
                outcome = None
            dispatch_index += 1

            if isinstance(outcome, HandlerSucceeded):
                finalization = self._receipt_repository.mark_processed(  # type: ignore[attr-defined]
                    decision.receipt_id
                )
                audit_outcome = "handler_processed"
            else:
                finalization = self._receipt_repository.mark_failed(  # type: ignore[attr-defined]
                    decision.receipt_id,
                    "handler_failed",
                )
                audit_outcome = "handler_failed"

            self._audit_event(audit_outcome, channel_public_id, event)
            if finalization == "failed":
                storage_failed = True
                self._audit_event("storage_unavailable", channel_public_id, event)
        return storage_failed

    @staticmethod
    def _envelope(
        channel_public_id: UUID,
        event: VerifiedEventData,
    ) -> VerifiedWebhookEvent:
        return VerifiedWebhookEvent(
            channel_public_id=channel_public_id,
            webhook_event_id=event.webhook_event_id,
            event_type=event.event_type,
            occurred_at_ms=event.occurred_at_ms,
            is_redelivery=event.is_redelivery,
            data=event.event,
        )

    def _audit_event(
        self,
        outcome: str,
        channel_public_id: UUID,
        event: VerifiedEventData,
    ) -> None:
        self._audit(
            outcome,
            channel_public_id=channel_public_id,
            webhook_event_id=event.webhook_event_id,
            event_type=event.event_type,
        )

    def _audit(
        self,
        outcome: str,
        *,
        channel_public_id: UUID | None = None,
        webhook_event_id: str | None = None,
        event_type: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        try:
            entry = WebhookAuditEntry(
                outcome=outcome,  # type: ignore[arg-type]
                observed_at=self._observed_at_clock(),
                channel_public_id=channel_public_id,
                webhook_event_id=webhook_event_id,
                event_type=event_type,
                elapsed_ms=elapsed_ms,
            )
            self._audit_logger.record(entry)  # type: ignore[attr-defined]
        except Exception:
            return
