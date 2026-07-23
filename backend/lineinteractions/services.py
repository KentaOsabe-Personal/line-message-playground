from __future__ import annotations

from collections.abc import Callable

from linechannels.repositories import CredentialRepository, LineChannelDirectory
from linechannels.types import (
    AccessToken,
    CredentialAvailable,
    CredentialUnavailable,
    LinkableChannelSummary,
)
from linewebhooks.types import (
    HandlerExecutionContext,
    HandlerFailed,
    HandlerOutcome,
    HandlerSucceeded,
    VerifiedWebhookEvent,
)

from .types import (
    ActionFailed,
    ActionNoChange,
    ActionRejected,
    ActionSucceeded,
    CommandDefinition,
    CommandRegistry,
    InteractionAccountDirectory,
    InteractionAuditRecord,
    InteractionAuditRepository,
    InteractionOutcome,
    InteractionParser,
    InvalidInteraction,
    LineReplyGateway,
    LinkedInteractionUserMissing,
    OperationKind,
    OutOfScopeInteraction,
    ParsedPostbackInteraction,
    ParsedTextInteraction,
    PostbackActionCommand,
    PostbackActionHandler,
    PostbackActionRegistry,
    ReplyAccepted,
    ReplyOutcome,
    ReplyRejected,
    ReplyTimeoutBudget,
    ReplyUnknown,
    VerifiedInteractionChannel,
    VerifiedInteractionUser,
)


_MINIMUM_REPLY_START_SECONDS = 0.300
_MAXIMUM_REPLY_TOTAL_SECONDS = 0.600
_REPLY_CLEANUP_RESERVE_SECONDS = 0.100


class DefaultInteractionService:
    def __init__(
        self,
        *,
        parser: InteractionParser,
        channel_directory: LineChannelDirectory,
        account_directory: InteractionAccountDirectory,
        command_registry: CommandRegistry,
        action_registry: PostbackActionRegistry,
        credential_repository: CredentialRepository,
        reply_gateway: LineReplyGateway,
        audit_repository: InteractionAuditRepository,
        monotonic: Callable[[], float],
    ) -> None:
        self._parser = parser
        self._channel_directory = channel_directory
        self._account_directory = account_directory
        self._command_registry = command_registry
        self._action_registry = action_registry
        self._credential_repository = credential_repository
        self._reply_gateway = reply_gateway
        self._audit_repository = audit_repository
        self._monotonic = monotonic

    def handle(
        self,
        event: VerifiedWebhookEvent,
        context: HandlerExecutionContext,
    ) -> HandlerOutcome:
        if event.event_type not in ("message", "postback"):
            return HandlerFailed()

        try:
            parsed = self._parser.parse(event)
        except Exception:
            return self._finish(
                event=event,
                interaction_outcome="processing_failed",
            )

        if isinstance(parsed, InvalidInteraction):
            return self._finish(event=event, interaction_outcome="invalid")
        if isinstance(parsed, OutOfScopeInteraction):
            return self._finish(event=event, interaction_outcome="out_of_scope")
        if not isinstance(parsed, (ParsedTextInteraction, ParsedPostbackInteraction)):
            return self._finish(
                event=event,
                interaction_outcome="processing_failed",
            )

        try:
            channel_summary = self._channel_directory.get(event.channel_public_id)
            if (
                not isinstance(channel_summary, LinkableChannelSummary)
                or not channel_summary.is_active
                or not channel_summary.provider_id
            ):
                return self._finish(event=event, interaction_outcome="unlinked")
            channel = VerifiedInteractionChannel(
                channel_public_id=channel_summary.public_id,
                provider_id=channel_summary.provider_id,
            )
            user = self._account_directory.resolve_linked(
                channel_public_id=channel.channel_public_id,
                provider_id=channel.provider_id,
                subject=parsed.subject,
            )
        except Exception:
            return self._finish(
                event=event,
                interaction_outcome="processing_failed",
            )

        if isinstance(user, LinkedInteractionUserMissing):
            return self._finish(event=event, interaction_outcome="unlinked")
        if not isinstance(user, VerifiedInteractionUser):
            return self._finish(
                event=event,
                interaction_outcome="processing_failed",
            )

        if isinstance(parsed, ParsedTextInteraction):
            return self._handle_command(
                event=event,
                parsed=parsed,
                channel=channel,
                context=context,
            )
        return self._handle_action(
            event=event,
            parsed=parsed,
            channel=channel,
            user=user,
            context=context,
        )

    def _handle_action(
        self,
        *,
        event: VerifiedWebhookEvent,
        parsed: ParsedPostbackInteraction,
        channel: VerifiedInteractionChannel,
        user: VerifiedInteractionUser,
        context: HandlerExecutionContext,
    ) -> HandlerOutcome:
        try:
            handler = self._action_registry.resolve(parsed.action_name)
        except Exception:
            return self._finish(
                event=event,
                interaction_outcome="processing_failed",
            )
        if handler is None:
            return self._finish(event=event, interaction_outcome="unknown")
        if not isinstance(handler, PostbackActionHandler):
            return self._finish(
                event=event,
                operation_kind="action",
                operation_identifier=parsed.action_name,
                interaction_outcome="handler_failed",
            )

        command = PostbackActionCommand(
            action_name=parsed.action_name,
            payload=parsed.payload,
            channel=channel,
            webhook_event_id=event.webhook_event_id,
            user=user,
            execution=context,
        )
        try:
            result = handler.handle(command)
        except Exception:
            result = ActionFailed()

        outcome_by_type: tuple[tuple[type[object], InteractionOutcome], ...] = (
            (ActionSucceeded, "action_succeeded"),
            (ActionNoChange, "action_no_change"),
            (ActionRejected, "action_rejected"),
            (ActionFailed, "handler_failed"),
        )
        interaction_outcome: InteractionOutcome = "handler_failed"
        for result_type, outcome in outcome_by_type:
            if isinstance(result, result_type):
                interaction_outcome = outcome
                break
        return self._finish(
            event=event,
            operation_kind="action",
            operation_identifier=parsed.action_name,
            interaction_outcome=interaction_outcome,
        )

    def _handle_command(
        self,
        *,
        event: VerifiedWebhookEvent,
        parsed: ParsedTextInteraction,
        channel: VerifiedInteractionChannel,
        context: HandlerExecutionContext,
    ) -> HandlerOutcome:
        try:
            command = self._command_registry.resolve(parsed.candidate)
        except Exception:
            return self._finish(
                event=event,
                interaction_outcome="processing_failed",
            )
        if command is None:
            return self._finish(event=event, interaction_outcome="unknown")
        if not isinstance(command, CommandDefinition):
            return self._finish(
                event=event,
                interaction_outcome="processing_failed",
            )

        try:
            credential = self._credential_repository.get_access_token(
                channel.channel_public_id
            )
        except Exception:
            return self._finish_command(
                event=event,
                command=command,
                interaction_outcome="processing_failed",
            )
        if isinstance(credential, CredentialUnavailable):
            return self._finish_command(
                event=event,
                command=command,
                interaction_outcome="credential_unavailable",
            )
        if (
            not isinstance(credential, CredentialAvailable)
            or not isinstance(credential.value, AccessToken)
        ):
            return self._finish_command(
                event=event,
                command=command,
                interaction_outcome="processing_failed",
            )

        cutoff = context.external_io_deadline_monotonic
        try:
            remaining = (
                min(
                    _MAXIMUM_REPLY_TOTAL_SECONDS,
                    cutoff - self._monotonic(),
                )
                if cutoff is not None
                else 0.0
            )
        except Exception:
            remaining = 0.0
        if remaining < _MINIMUM_REPLY_START_SECONDS:
            return self._finish_command(
                event=event,
                command=command,
                interaction_outcome="deadline_exceeded",
            )

        timeout = ReplyTimeoutBudget(
            min(
                _MAXIMUM_REPLY_TOTAL_SECONDS - _REPLY_CLEANUP_RESERVE_SECONDS,
                remaining - _REPLY_CLEANUP_RESERVE_SECONDS,
            )
        )
        try:
            reply_result = self._reply_gateway.reply_text(
                access_token=credential.value,
                reply_token=parsed.reply_token,
                text=command.reply_text,
                timeout=timeout,
            )
        except Exception:
            reply_result = ReplyUnknown()

        if isinstance(reply_result, ReplyAccepted):
            reply_outcome: ReplyOutcome = "accepted"
        elif isinstance(reply_result, ReplyRejected):
            reply_outcome = "rejected"
        else:
            reply_outcome = "unknown"
        return self._finish_command(
            event=event,
            command=command,
            interaction_outcome="command_processed",
            reply_outcome=reply_outcome,
        )

    def _finish_command(
        self,
        *,
        event: VerifiedWebhookEvent,
        command: CommandDefinition,
        interaction_outcome: InteractionOutcome,
        reply_outcome: ReplyOutcome = "not_started",
    ) -> HandlerOutcome:
        return self._finish(
            event=event,
            operation_kind="command",
            operation_identifier=command.identifier,
            interaction_outcome=interaction_outcome,
            reply_outcome=reply_outcome,
        )

    def _finish(
        self,
        *,
        event: VerifiedWebhookEvent,
        interaction_outcome: InteractionOutcome,
        operation_kind: OperationKind = "none",
        operation_identifier: str | None = None,
        reply_outcome: ReplyOutcome = "not_started",
    ) -> HandlerOutcome:
        try:
            record_result = self._audit_repository.record(
                InteractionAuditRecord(
                    channel_public_id=event.channel_public_id,
                    webhook_event_id=event.webhook_event_id,
                    event_type=event.event_type,
                    operation_kind=operation_kind,
                    operation_identifier=operation_identifier,
                    interaction_outcome=interaction_outcome,
                    reply_outcome=reply_outcome,
                )
            )
        except Exception:
            return HandlerFailed()
        if record_result != "recorded":
            return HandlerFailed()
        if interaction_outcome in {
            "unknown",
            "invalid",
            "out_of_scope",
            "unlinked",
            "action_succeeded",
            "action_no_change",
            "action_rejected",
        } or (
            interaction_outcome == "command_processed"
            and reply_outcome == "accepted"
        ):
            return HandlerSucceeded()
        return HandlerFailed()
