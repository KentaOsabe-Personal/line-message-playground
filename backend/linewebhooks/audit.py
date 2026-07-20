import logging

from .types import WebhookAuditEntry


class SafeWebhookAuditLogger:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("linewebhooks.audit")

    def record(self, entry: WebhookAuditEntry) -> None:
        self._logger.info(
            "line_webhook_audit",
            extra={
                "audit_outcome": entry.outcome,
                "audit_observed_at": entry.observed_at.isoformat(),
                "audit_channel_public_id": (
                    str(entry.channel_public_id)
                    if entry.channel_public_id is not None
                    else None
                ),
                "audit_webhook_event_id": entry.webhook_event_id,
                "audit_event_type": entry.event_type,
                "audit_elapsed_ms": entry.elapsed_ms,
            },
        )
