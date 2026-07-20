import inspect
import logging
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

from django.test import SimpleTestCase

from linewebhooks.audit import SafeWebhookAuditLogger
from linewebhooks.types import WebhookAuditEntry


class SafeWebhookAuditLoggerTests(SimpleTestCase):
    OUTCOMES = (
        "channel_rejected",
        "signature_rejected",
        "payload_rejected",
        "empty_accepted",
        "event_accepted",
        "event_duplicate",
        "event_unsupported",
        "handler_processed",
        "handler_failed",
        "storage_unavailable",
        "response_deadline_exceeded",
    )

    # テストケース: すべての運用結果を安全な監査 logger へ記録する
    # 期待値: 固定 message と whitelist 済み field だけが構造化ログへ渡る
    def test_records_each_outcome_with_only_whitelisted_fields(self) -> None:
        logger = Mock(spec=logging.Logger)
        audit = SafeWebhookAuditLogger(logger)
        observed_at = datetime.now(UTC)
        channel_public_id = uuid4()

        for outcome in self.OUTCOMES:
            entry = WebhookAuditEntry(
                outcome=outcome,  # type: ignore[arg-type]
                observed_at=observed_at,
                channel_public_id=channel_public_id,
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                event_type="message",
                elapsed_ms=2000 if outcome == "response_deadline_exceeded" else None,
            )
            audit.record(entry)

        self.assertEqual(logger.info.call_count, len(self.OUTCOMES))
        for outcome, call in zip(self.OUTCOMES, logger.info.call_args_list):
            self.assertEqual(call.args, ("line_webhook_audit",))
            self.assertEqual(set(call.kwargs), {"extra"})
            self.assertEqual(
                call.kwargs["extra"],
                {
                    "audit_outcome": outcome,
                    "audit_observed_at": observed_at.isoformat(),
                    "audit_channel_public_id": str(channel_public_id),
                    "audit_webhook_event_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                    "audit_event_type": "message",
                    "audit_elapsed_ms": (
                        2000 if outcome == "response_deadline_exceeded" else None
                    ),
                },
            )

    # テストケース: deadline 以外の監査 entry を通常ログへ記録する
    # 期待値: elapsed milliseconds と例外・traceback 情報をログへ渡さない
    def test_ordinary_outcome_omits_elapsed_and_exception_context(self) -> None:
        logger = Mock(spec=logging.Logger)
        audit = SafeWebhookAuditLogger(logger)

        audit.record(
            WebhookAuditEntry(
                outcome="payload_rejected",
                observed_at=datetime.now(UTC),
            )
        )

        kwargs = logger.info.call_args.kwargs
        self.assertIsNone(kwargs["extra"]["audit_elapsed_ms"])
        self.assertNotIn("exc_info", kwargs)
        self.assertNotIn("stack_info", kwargs)

    # テストケース: 監査入口と通常ログへ禁止データ canary の格納口を探す
    # 期待値: body、署名、秘密値、event data、生例外、任意 context を受け取れない
    def test_audit_entry_point_has_no_forbidden_inputs(self) -> None:
        forbidden = {
            "raw_body",
            "signature",
            "secret",
            "event_data",
            "exception",
            "context",
        }

        self.assertTrue(
            forbidden.isdisjoint(inspect.signature(WebhookAuditEntry).parameters)
        )
        self.assertEqual(
            tuple(inspect.signature(SafeWebhookAuditLogger.record).parameters),
            ("self", "entry"),
        )
