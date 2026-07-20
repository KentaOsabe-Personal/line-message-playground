import logging
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase

from linewebhooks.audit import SafeWebhookAuditLogger
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.repositories import DjangoEventReceiptRepository
from linewebhooks.tests.support import (
    CHANNEL_ID,
    CHANNEL_SECRET,
    EVENT_IDS,
    RecordingHandler,
    build_service,
    event,
    signed_payload,
)
from linewebhooks.views import WebhookAPIView


RAW_CANARY = "raw-body-canary"
SIGNATURE_CANARY = "signature-canary"
USER_CANARY = "U-user-source-canary"
REPLY_CANARY = "reply-token-canary"
MESSAGE_CANARY = "message-content-canary"
POSTBACK_CANARY = "postback-content-canary"
EXCEPTION_CANARY = "raw-exception-canary"
FORBIDDEN_CANARIES = (
    RAW_CANARY,
    SIGNATURE_CANARY,
    CHANNEL_SECRET,
    USER_CANARY,
    REPLY_CANARY,
    MESSAGE_CANARY,
    POSTBACK_CANARY,
    EXCEPTION_CANARY,
)


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class WebhookSecurityIntegrationTests(TestCase):
    def setUp(self) -> None:
        self.log_handler = _ListHandler()
        self.logger = logging.getLogger(f"linewebhooks.security-test.{id(self)}")
        self.logger.handlers = [self.log_handler]
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    def _post(self, service: object, raw_body: bytes, signature: str):
        with patch.object(WebhookAPIView, "service_factory", return_value=service):
            return self.client.post(
                f"/api/line/webhooks/{CHANNEL_ID}/",
                data=raw_body,
                content_type="application/json",
                HTTP_X_LINE_SIGNATURE=signature,
            )

    def _assert_canaries_absent(self, *surfaces: object) -> None:
        rendered = "\n".join(str(surface) for surface in surfaces)
        for canary in FORBIDDEN_CANARIES:
            self.assertNotIn(canary, rendered)

    # テストケース: user・reply・message・postback内容を持つ署名済みeventでhandler生例外を発生させる
    # 期待値: safe表現・model・通常監査log・公開responseに禁止canaryとtracebackが残らない
    def test_forbidden_data_is_absent_across_success_and_handler_failure_boundaries(
        self,
    ) -> None:
        handler = RecordingHandler(RuntimeError(EXCEPTION_CANARY))
        audit_logger = SafeWebhookAuditLogger(self.logger)
        service, _ = build_service(handler=handler, audit_logger=audit_logger)  # type: ignore[arg-type]
        raw_body, signature = signed_payload(
            [
                event(
                    EVENT_IDS[0],
                    extra={
                        "source": {"userId": USER_CANARY},
                        "replyToken": REPLY_CANARY,
                        "message": {"text": MESSAGE_CANARY},
                        "postback": {"data": POSTBACK_CANARY},
                        "rawMarker": RAW_CANARY,
                    },
                )
            ]
        )

        response = self._post(service, raw_body, signature)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(len(handler.events), 1)
        envelope = handler.events[0]
        receipt = WebhookEventReceipt.objects.get()
        self.assertEqual(receipt.status, "failed")
        self.assertNotIn("data", repr(envelope))
        self.assertFalse(
            {
                "raw_body",
                "signature",
                "channel_secret",
                "destination",
                "exception",
            }
            & set(vars(type(envelope)).get("__dataclass_fields__", {}))
        )
        log_surfaces = [
            record.getMessage() for record in self.log_handler.records
        ] + [record.__dict__ for record in self.log_handler.records]
        self._assert_canaries_absent(
            response.content,
            repr(envelope),
            str(receipt),
            *log_surfaces,
        )
        self.assertNotIn("traceback", str(log_surfaces).lower())
        model_fields = {field.name for field in WebhookEventReceipt._meta.fields}
        self.assertFalse(
            {
                "raw_body",
                "signature",
                "channel_secret",
                "destination",
                "user_id",
                "reply_token",
                "message",
                "postback",
            }
            & model_fields
        )

    # テストケース: 署名拒否と受付保存例外に本文・署名・生例外canaryを含める
    # 期待値: 固定401/503以外を公開せず、通常監査logとDBへ禁止canaryを残さない
    def test_rejection_and_storage_exception_do_not_expose_internal_data(self) -> None:
        handler = RecordingHandler()
        audit_logger = SafeWebhookAuditLogger(self.logger)
        repository = DjangoEventReceiptRepository()
        service, _ = build_service(
            handler=handler,
            receipt_repository=repository,
            audit_logger=audit_logger,  # type: ignore[arg-type]
        )
        raw_body, valid_signature = signed_payload(
            [event(EVENT_IDS[0], extra={"rawMarker": RAW_CANARY})]
        )

        rejected = self._post(service, raw_body, SIGNATURE_CANARY)
        with patch.object(
            repository,
            "_create_receipt",
            side_effect=DatabaseError(EXCEPTION_CANARY),
        ):
            unavailable = self._post(service, raw_body, valid_signature)

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(
            rejected.json(), {"error": {"code": "webhook_rejected"}}
        )
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json(), {"error": {"code": "webhook_unavailable"}}
        )
        self.assertEqual(WebhookEventReceipt.objects.count(), 0)
        self.assertEqual(handler.events, [])
        log_surfaces = [record.__dict__ for record in self.log_handler.records]
        self._assert_canaries_absent(
            rejected.content,
            unavailable.content,
            *log_surfaces,
        )
        self.assertTrue(
            all(record.exc_info is None for record in self.log_handler.records)
        )
