from typing import Literal

from django.db import DatabaseError, transaction

from .models import InteractionAudit
from .types import InteractionAuditRecord


class DjangoInteractionAuditRepository:
    def __init__(self, using: str = "default") -> None:
        self.using = using

    def record(
        self,
        audit: InteractionAuditRecord,
    ) -> Literal["recorded", "failed"]:
        try:
            with transaction.atomic(using=self.using):
                InteractionAudit.objects.using(self.using).create(
                    channel_public_id=audit.channel_public_id,
                    webhook_event_id=audit.webhook_event_id,
                    event_type=audit.event_type,
                    operation_kind=audit.operation_kind,
                    operation_identifier=audit.operation_identifier,
                    interaction_outcome=audit.interaction_outcome,
                    reply_outcome=audit.reply_outcome,
                )
        except DatabaseError:
            return "failed"
        return "recorded"
