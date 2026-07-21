import math

from django.db import transaction
from linechannels.repositories import LineChannelDirectory
from linewebhooks.types import (
    HandlerFailed,
    HandlerOutcome,
    HandlerSucceeded,
    VerifiedWebhookEvent,
)

from .types import (
    AccountProjectionRepository,
    FriendshipAuditRecord,
    FriendshipAuditRepository,
    FriendshipEventParser,
    InvalidFriendshipEvent,
    LockedRecipientProjection,
    OutOfScopeSource,
    ProjectionOutcome,
    ProjectionTargetMissing,
    ValidatedFriendshipEvent,
)


def decide_projection(
    target: LockedRecipientProjection,
    event: ValidatedFriendshipEvent,
) -> ProjectionOutcome:
    baseline_ms = math.floor(target.registered_at.timestamp() * 1000)
    if event.occurred_at_ms <= baseline_ms:
        return "stale"

    if target.last_webhook_event_id == event.webhook_event_id:
        return "duplicate"

    if target.last_occurred_at_ms is not None:
        assert target.last_webhook_event_id is not None
        last_key = (
            target.last_occurred_at_ms,
            target.last_webhook_event_id.encode("ascii"),
        )
        event_key = (
            event.occurred_at_ms,
            event.webhook_event_id.encode("ascii"),
        )
        if event_key <= last_key:
            return "stale"

    if target.friendship_state == event.target_state:
        return "state_maintained"
    return "applied"


class DefaultFriendshipSyncService:
    def __init__(
        self,
        parser: FriendshipEventParser,
        channel_directory: LineChannelDirectory,
        account_repository: AccountProjectionRepository,
        audit_repository: FriendshipAuditRepository,
        using: str = "default",
    ) -> None:
        self.parser = parser
        self.channel_directory = channel_directory
        self.account_repository = account_repository
        self.audit_repository = audit_repository
        self.using = using

    def handle(self, event: VerifiedWebhookEvent) -> HandlerOutcome:
        try:
            parsed = self.parser.parse(event)
            if isinstance(parsed, InvalidFriendshipEvent):
                self._record_non_update(event, "invalid")
                return HandlerSucceeded()
            if isinstance(parsed, OutOfScopeSource):
                self._record_non_update(event, "out_of_scope")
                return HandlerSucceeded()
            if not isinstance(parsed, ValidatedFriendshipEvent):
                raise RuntimeError("invalid_parser_contract")

            channel = self.channel_directory.get(parsed.channel_public_id)
            if channel is None:
                self._record(parsed, "unresolvable")
                return HandlerSucceeded()

            with transaction.atomic(using=self.using):
                target = self.account_repository.lock_target(
                    channel_public_id=parsed.channel_public_id,
                    provider_id=channel.provider_id,
                    subject=parsed.subject,
                )
                if isinstance(target, ProjectionTargetMissing):
                    self.audit_repository.record(
                        self._audit(parsed, "unlinked")
                    )
                    return HandlerSucceeded()
                if not isinstance(target, LockedRecipientProjection):
                    raise RuntimeError("invalid_account_contract")

                outcome = decide_projection(target, parsed)
                if outcome in ("applied", "state_maintained"):
                    self.account_repository.apply_locked(
                        target,
                        friendship_state=parsed.target_state,
                        occurred_at_ms=parsed.occurred_at_ms,
                        webhook_event_id=parsed.webhook_event_id,
                    )
                self.audit_repository.record(self._audit(parsed, outcome))
            return HandlerSucceeded()
        except Exception:
            return HandlerFailed()

    def _record_non_update(
        self,
        event: VerifiedWebhookEvent,
        outcome: ProjectionOutcome,
    ) -> None:
        audit = FriendshipAuditRecord(
            channel_public_id=event.channel_public_id,
            webhook_event_id=event.webhook_event_id,
            event_type=event.event_type,  # type: ignore[arg-type]
            occurred_at_ms=event.occurred_at_ms,
            outcome=outcome,
            is_unblocked=None,
        )
        with transaction.atomic(using=self.using):
            self.audit_repository.record(audit)

    def _record(
        self,
        event: ValidatedFriendshipEvent,
        outcome: ProjectionOutcome,
    ) -> None:
        with transaction.atomic(using=self.using):
            self.audit_repository.record(self._audit(event, outcome))

    @staticmethod
    def _audit(
        event: ValidatedFriendshipEvent,
        outcome: ProjectionOutcome,
    ) -> FriendshipAuditRecord:
        return FriendshipAuditRecord(
            channel_public_id=event.channel_public_id,
            webhook_event_id=event.webhook_event_id,
            event_type=event.event_type,
            occurred_at_ms=event.occurred_at_ms,
            outcome=outcome,
            is_unblocked=event.is_unblocked,
        )
