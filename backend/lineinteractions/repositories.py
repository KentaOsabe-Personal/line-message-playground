from typing import Literal

from django.db import DatabaseError, transaction

from linechannels.reference_fence import (
    ChannelReferenceFence,
)

from .models import InteractionAudit
from .types import InteractionAuditRecord


class DjangoInteractionAuditRepository:
    def __init__(
        self,
        reference_fence: ChannelReferenceFence,
        *,
        using: str = "default",
    ) -> None:
        self.using = using
        self._reference_fence = reference_fence

    def record(
        self,
        audit: InteractionAuditRecord,
    ) -> Literal["recorded", "failed"]:
        try:
            with transaction.atomic(using=self.using):
                fence_result = self._reference_fence.lock_existing(
                    audit.channel_public_id
                )
                if fence_result.status != "locked":
                    return fence_result.status
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

    def reserve(self, audit: InteractionAuditRecord) -> str:
        return self.record(audit)

    def replace_reserved(self, audit: InteractionAuditRecord) -> str:
        try:
            with transaction.atomic(using=self.using):
                updated = InteractionAudit.objects.using(self.using).filter(
                    webhook_event_id=audit.webhook_event_id,
                    channel_public_id=audit.channel_public_id,
                ).update(
                    event_type=audit.event_type,
                    operation_kind=audit.operation_kind,
                    operation_identifier=audit.operation_identifier,
                    interaction_outcome=audit.interaction_outcome,
                    reply_outcome=audit.reply_outcome,
                )
        except DatabaseError:
            return "failed"
        return "recorded" if updated == 1 else "failed"


class DjangoInteractionReferenceProbe:
    def __init__(self, using: str = "default") -> None:
        self.using = using

    def is_referenced(self, channel_public_id) -> bool:
        return InteractionAudit.objects.using(self.using).filter(
            channel_public_id=channel_public_id
        ).exists()
