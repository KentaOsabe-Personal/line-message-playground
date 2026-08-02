from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Callable, Protocol, runtime_checkable
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from lineaccounts.admin_authorization import (
    OwnerActiveProof,
    OwnerFenceFailed,
    OwnerOperationContext,
    OwnerOperationFence,
)
from linechannels.admin_types import (
    ChannelRevisionProof,
    ChannelSnapshotCommand,
    ExactChannelSnapshotAvailable,
    ExactChannelSnapshotRejected,
    OwnerChannelOperationPort,
    RichMenuChannelSnapshot,
)

from .catalog import DefaultTemplateCatalog
from .confirmation import DefaultRichMenuConfirmation
from .gateway import (
    GatewayAccepted,
    CreateAccepted,
    GatewayRejected,
    GatewayUnknown,
    RichMenuArea,
    RichMenuBounds,
    RichMenuDefaultPresent,
    RichMenuGateway,
    RichMenuGatewayContext,
    RichMenuObject,
    RichMenuUriAction,
)
from .reconciliation import (
    DefaultRichMenuReconciler,
    ManagedResourceTarget,
    ReconcileContext,
    Reconciliation,
    RecheckConfirmed,
    RecheckContext,
    RecheckUnknown,
)
from .renderer import DefaultDeterministicRenderer
from .repository import (
    AcceptedOperation,
    OperationAccepted,
    OperationConflict,
    OperationReplay,
    OwnerChannelScope,
    RecoveryAccepted,
    RecoveryHandoffResult,
    RecoveryOutcome,
    StageClaimed,
    StageConflict,
    StageExpired,
    StageOutcome,
)
from .types import (
    ChannelStateView,
    ConfirmationRejected,
    ConfirmationAccepted,
    DefaultObservation,
    HistoryPage,
    HistorySummary,
    InputFieldError,
    InputRejected,
    IssuedConfirmation,
    NextAllowedAction,
    NormalizedTemplate,
    ObservationKind,
    OperationView,
    OperationCommand,
    OperationKind,
    OperationStage,
    OperationStatus,
    PreviewCommand,
    PreviewSnapshot,
    PreviewView,
    PreviewWarning,
    RenderRejected,
    RenderedImage,
    SafeResultCode,
    TemplateInput,
    ResourceLifecycle,
)

from .types import IntegrationNotReady, MutationReady


@runtime_checkable
class MutationReadiness(Protocol):
    def authorize(
        self, kind: OperationKind
    ) -> MutationReady | IntegrationNotReady: ...


class DefaultMutationReadiness:
    _RECOVERY_KINDS = frozenset(
        {
            OperationKind.UNLINK,
            OperationKind.RELEASE,
            OperationKind.RECHECK,
            OperationKind.CLEANUP,
        }
    )

    def __init__(self, *, mode: str, integration_complete: bool) -> None:
        self._mode = mode
        self.configuration_valid = mode in {
            "read_only",
            "recovery_only",
            "enabled",
        } and (mode == "read_only" or integration_complete)

    def authorize(
        self, kind: OperationKind
    ) -> MutationReady | IntegrationNotReady:
        if not isinstance(kind, OperationKind):
            return IntegrationNotReady(reason="unsupported_operation")
        if not self.configuration_valid or self._mode == "read_only":
            return IntegrationNotReady(reason="integration_not_ready")
        if self._mode == "recovery_only" and kind not in self._RECOVERY_KINDS:
            return IntegrationNotReady(reason="integration_not_ready")
        return MutationReady()


@dataclass(frozen=True, slots=True, repr=False)
class PreviewRequest:
    """Owner-facing preview input before template normalization."""

    channel_public_id: UUID
    expected_channel_revision: datetime
    template: TemplateInput | NormalizedTemplate

    def __post_init__(self) -> None:
        if not isinstance(self.channel_public_id, UUID):
            raise ValueError("invalid preview channel")
        if (
            not isinstance(self.expected_channel_revision, datetime)
            or timezone.is_naive(self.expected_channel_revision)
        ):
            raise ValueError("invalid preview channel revision")
        if not isinstance(self.template, (TemplateInput, NormalizedTemplate)):
            raise ValueError("invalid preview template input")

    def __repr__(self) -> str:
        return (
            f"<PreviewRequest channel_public_id={self.channel_public_id} "
            "expected_channel_revision=redacted template=redacted>"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ServiceFailed:
    code: SafeResultCode
    next_allowed_actions: tuple[NextAllowedAction, ...] = ()
    errors: tuple[InputFieldError, ...] = ()
    status: str = "failed"

    def __post_init__(self) -> None:
        if not isinstance(self.code, SafeResultCode):
            raise ValueError("invalid service failure code")
        if not isinstance(self.next_allowed_actions, tuple) or not all(
            isinstance(action, NextAllowedAction)
            for action in self.next_allowed_actions
        ):
            raise ValueError("invalid service failure actions")
        if not isinstance(self.errors, tuple) or not all(
            isinstance(error, InputFieldError) for error in self.errors
        ):
            raise ValueError("invalid service failure errors")

    @property
    def token(self) -> None:
        return None

    @property
    def image_base64(self) -> None:
        return None

    def __repr__(self) -> str:
        return (
            f"<ServiceFailed code={self.code.value!r} "
            f"next_allowed_actions={self.next_allowed_actions!r} "
            f"error_count={len(self.errors)}>"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PreviewSucceeded:
    preview: PreviewView
    confirmation: IssuedConfirmation
    image: RenderedImage
    status: str = "succeeded"

    def __post_init__(self) -> None:
        if not isinstance(self.preview, PreviewView):
            raise ValueError("invalid preview result")
        if not isinstance(self.confirmation, IssuedConfirmation):
            raise ValueError("invalid preview confirmation")
        if not isinstance(self.image, RenderedImage):
            raise ValueError("invalid preview image")

    @property
    def token(self) -> str:
        return self.confirmation.token

    @property
    def expires_at(self) -> datetime:
        return self.confirmation.expires_at

    @property
    def image_base64(self) -> str:
        return base64.b64encode(self.image.binary).decode("ascii")

    def __repr__(self) -> str:
        return (
            f"<PreviewSucceeded channel_public_id={self.preview.channel_public_id} "
            f"image_digest={self.image.pixel_digest} token=redacted binary=redacted>"
        )


PreviewResult = PreviewSucceeded | ServiceFailed


@dataclass(frozen=True, slots=True, repr=False)
class StateSucceeded:
    state: ChannelStateView
    status: str = "succeeded"

    def __post_init__(self) -> None:
        if not isinstance(self.state, ChannelStateView):
            raise ValueError("invalid state result")

    def __repr__(self) -> str:
        return f"<StateSucceeded channel_public_id={self.state.channel_public_id}>"


@dataclass(frozen=True, slots=True, repr=False)
class OperationSucceeded:
    operation: OperationView
    status: str = "succeeded"

    def __post_init__(self) -> None:
        if not isinstance(self.operation, OperationView):
            raise ValueError("invalid operation result")

    def __repr__(self) -> str:
        return f"<OperationSucceeded operation_id={self.operation.operation_id}>"


@dataclass(frozen=True, slots=True, repr=False)
class HistorySucceeded:
    history: HistoryPage
    status: str = "succeeded"

    def __post_init__(self) -> None:
        if not isinstance(self.history, HistoryPage):
            raise ValueError("invalid history result")

    def __repr__(self) -> str:
        return f"<HistorySucceeded entry_count={len(self.history.entries)}>"


StateResult = StateSucceeded | ServiceFailed
OperationResult = OperationSucceeded | ServiceFailed
HistoryResult = HistorySucceeded | ServiceFailed


class RichMenuRepositoryPort(Protocol):
    def list_managed_resources(
        self, scope: OwnerChannelScope
    ) -> tuple[ManagedResourceTarget, ...]: ...

    def record_observation(
        self, scope: OwnerChannelScope, observation: DefaultObservation
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class _ResolvedOwnerChannel:
    proof: OwnerActiveProof
    snapshot: RichMenuChannelSnapshot | None


class DefaultRichMenuService:
    """Application orchestration for the owner-facing rich-menu foundation."""

    def __init__(
        self,
        *,
        owner_fence: OwnerOperationFence,
        channel_port: OwnerChannelOperationPort,
        repository: RichMenuRepositoryPort,
        gateway: RichMenuGateway,
        reconciler=None,
        catalog=None,
        renderer=None,
        confirmation=None,
        readiness: MutationReadiness | None = None,
        using: str = "default",
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._owner_fence = owner_fence
        self._channel_port = channel_port
        self._repository = repository
        self._gateway = gateway
        self._reconciler = reconciler or DefaultRichMenuReconciler(gateway)
        self._catalog = catalog or DefaultTemplateCatalog()
        self._renderer = renderer or DefaultDeterministicRenderer(catalog=self._catalog)
        self._confirmation = confirmation or DefaultRichMenuConfirmation()
        self._readiness = readiness or DefaultMutationReadiness(
            mode="read_only", integration_complete=False
        )
        self._using = using
        self._clock = clock

    def preview(
        self,
        owner: OwnerOperationContext,
        command: PreviewRequest | PreviewCommand,
    ) -> PreviewResult:
        if not isinstance(owner, OwnerOperationContext):
            return ServiceFailed(SafeResultCode.AUTHENTICATION_REQUIRED)
        request = self._coerce_preview_request(command)
        if request is None:
            return ServiceFailed(SafeResultCode.INVALID_INPUT)

        resolved, failure = self._resolve_owner_channel(owner, request)
        if failure is not None:
            return failure
        assert resolved is not None
        snapshot = resolved.snapshot
        if snapshot is None:
            return ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        if not snapshot.is_active:
            return ServiceFailed(SafeResultCode.CHANNEL_INACTIVE)

        normalized, input_failure = self._normalize_template(request.template)
        if input_failure is not None:
            return input_failure
        assert normalized is not None

        rendered = self._renderer.render(normalized)
        if isinstance(rendered, RenderRejected):
            return ServiceFailed(rendered.code, errors=rendered.errors)
        if not isinstance(rendered, RenderedImage):
            return ServiceFailed(SafeResultCode.IMAGE_INVALID)

        try:
            rich_menu = self._build_rich_menu_object(normalized)
        except (TypeError, ValueError):
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        gateway_context = RichMenuGatewayContext(
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
            access_token=snapshot.access_token,
        )
        try:
            validation = self._gateway.validate(gateway_context, rich_menu)
        except Exception:
            return ServiceFailed(SafeResultCode.RESPONSE_UNKNOWN)
        validation_failure = self._map_gateway_mutation_failure(validation)
        if validation_failure is not None:
            return validation_failure

        scope_failure = self._verify_scope_unchanged(owner, snapshot)
        if scope_failure is not None:
            return scope_failure

        scope = OwnerChannelScope(
            owner_identity_public_id=resolved.proof.identity_public_id,
            provider_id=resolved.proof.provider_id,
            channel_public_id=snapshot.channel_public_id,
        )
        reconciliation = self._observe(scope, gateway_context)
        if isinstance(reconciliation, ServiceFailed):
            return reconciliation
        observation = reconciliation.observation
        if observation.kind is ObservationKind.UNKNOWN:
            return ServiceFailed(
                SafeResultCode.OBSERVATION_UNKNOWN,
                next_allowed_actions=(
                    NextAllowedAction.RECHECK,
                    NextAllowedAction.GET_STATE,
                ),
            )

        scope_failure = self._verify_scope_unchanged(owner, snapshot)
        if scope_failure is not None:
            return scope_failure
        observation_failure = self._record_observation(scope, observation)
        if observation_failure is not None:
            return observation_failure

        preview_snapshot = PreviewSnapshot(
            owner_identity=resolved.proof.identity_public_id,
            provider_id=resolved.proof.provider_id,
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
            default_observation_fingerprint=observation.fingerprint,
            template=normalized,
            pixel_digest=rendered.pixel_digest,
        )
        issued = self._confirmation.issue(preview_snapshot, self._clock())
        if not isinstance(issued, IssuedConfirmation):
            return ServiceFailed(SafeResultCode.UNEXPECTED)
        warnings = (
            (PreviewWarning.EXTERNAL_DEFAULT_REPLACED,)
            if observation.kind is ObservationKind.EXTERNAL_DEFAULT
            else ()
        ) + (
            PreviewWarning.URL_HISTORY_PERSISTED,
            PreviewWarning.URL_MUST_NOT_CONTAIN_SECRETS,
        )
        return PreviewSucceeded(
            preview=PreviewView(
                channel_public_id=snapshot.channel_public_id,
                channel_label=snapshot.channel_label,
                template=normalized,
                image_digest=rendered.pixel_digest,
                observation=observation,
                expires_at=issued.expires_at,
                warnings=warnings,
            ),
            confirmation=issued,
            image=rendered,
        )

    def get_state(
        self,
        owner: OwnerOperationContext,
        channel_id: UUID,
        *,
        expected_channel_revision: datetime | None = None,
    ) -> StateResult:
        if not isinstance(owner, OwnerOperationContext) or not isinstance(
            channel_id, UUID
        ):
            return ServiceFailed(SafeResultCode.AUTHENTICATION_REQUIRED)
        resolved, inactive, failure = self._resolve_state_channel(
            owner,
            channel_id,
            expected_channel_revision=expected_channel_revision,
        )
        if failure is not None:
            return failure
        if resolved is None:
            return ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        scope = OwnerChannelScope(
            owner_identity_public_id=resolved.proof.identity_public_id,
            provider_id=resolved.proof.provider_id,
            channel_public_id=channel_id,
        )
        stored = self._repository.get_state(scope)
        state = stored if isinstance(stored, ChannelStateView) else self._empty_state(channel_id)
        if inactive or resolved.snapshot is None:
            return StateSucceeded(state)

        gateway_context = RichMenuGatewayContext(
            channel_public_id=resolved.snapshot.channel_public_id,
            channel_revision=resolved.snapshot.channel_revision,
            access_token=resolved.snapshot.access_token,
        )
        reconciliation = self._observe(scope, gateway_context)
        if isinstance(reconciliation, ServiceFailed):
            return reconciliation
        scope_failure = self._verify_scope_unchanged(owner, resolved.snapshot)
        if scope_failure is not None:
            return scope_failure
        observation_failure = self._record_observation(scope, reconciliation.observation)
        if observation_failure is not None:
            return observation_failure
        refreshed = self._repository.get_state(scope)
        state = refreshed if isinstance(refreshed, ChannelStateView) else state
        return StateSucceeded(
            state=state.__class__(
                channel_public_id=state.channel_public_id,
                current_resource=state.current_resource,
                blocking_operation=state.blocking_operation,
                active_operation=state.active_operation,
                cleanup_resources=state.cleanup_resources,
                latest_observation=reconciliation.observation,
                history_summary=state.history_summary,
                next_allowed_actions=reconciliation.next_allowed_actions,
            )
        )

    def get_operation(
        self, owner: OwnerOperationContext, operation_id: UUID
    ) -> OperationResult:
        proof, failure = self._lock_owner(owner)
        if failure is not None:
            return failure
        if not isinstance(operation_id, UUID):
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        reader = getattr(self._repository, "get_operation_for_owner", None)
        if callable(reader):
            operation = reader(proof.identity_public_id, proof.provider_id, operation_id)
        else:
            reader = getattr(self._repository, "get_operation", None)
            if not callable(reader):
                return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
            try:
                operation = reader(
                    OwnerChannelScope(
                        owner_identity_public_id=proof.identity_public_id,
                        provider_id=proof.provider_id,
                        channel_public_id=operation_id,
                    ),
                    operation_id,
                )
            except TypeError:
                operation = reader(proof.identity_public_id, proof.provider_id, operation_id)
        if not isinstance(operation, OperationView):
            return ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        return OperationSucceeded(operation)

    def list_history(
        self, owner: OwnerOperationContext, query
    ) -> HistoryResult:
        proof, failure = self._lock_owner(owner)
        if failure is not None:
            return failure
        try:
            scope = query.scope
            if (
                not isinstance(scope, OwnerChannelScope)
                or scope.owner_identity_public_id != proof.identity_public_id
                or scope.provider_id != proof.provider_id
            ):
                return ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
            history = self._repository.list_history(query)
        except (TypeError, ValueError):
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if not isinstance(history, HistoryPage):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return HistorySucceeded(history)

    def start_operation(
        self, owner: OwnerOperationContext, command: OperationCommand
    ) -> OperationResult:
        if not isinstance(owner, OwnerOperationContext):
            return ServiceFailed(SafeResultCode.AUTHENTICATION_REQUIRED)
        if not isinstance(command, OperationCommand):
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        readiness = self._readiness.authorize(command.kind)
        if isinstance(readiness, IntegrationNotReady):
            return ServiceFailed(SafeResultCode.INTEGRATION_NOT_READY)
        handlers = {
            OperationKind.APPLY: self._start_apply,
            OperationKind.UNLINK: self._start_unlink,
            OperationKind.RELEASE: self._start_release,
            OperationKind.RECHECK: self._start_recheck,
            OperationKind.CLEANUP: self._start_cleanup,
        }
        handler = handlers.get(command.kind)
        if handler is None:
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        return handler(owner, command)

    def _start_apply(
        self, owner: OwnerOperationContext, command: OperationCommand
    ) -> OperationResult:
        """受付とapplyのcreate/upload段階を一つの安全なworkflowへ束ねる。"""

        if not isinstance(owner, OwnerOperationContext):
            return ServiceFailed(SafeResultCode.AUTHENTICATION_REQUIRED)
        if not isinstance(command, OperationCommand):
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        readiness = self._readiness.authorize(command.kind)
        if isinstance(readiness, IntegrationNotReady):
            return ServiceFailed(SafeResultCode.INTEGRATION_NOT_READY)
        if command.kind is not OperationKind.APPLY:
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        if command.confirmation_token is None or command.template is None:
            return ServiceFailed(SafeResultCode.INVALID_INPUT)

        proof, owner_failure = self._lock_owner(owner)
        if owner_failure is not None:
            return owner_failure
        assert proof is not None
        normalized, input_failure = self._normalize_template(command.template)
        if input_failure is not None:
            return input_failure
        assert normalized is not None
        rendered = self._renderer.render(normalized)
        if isinstance(rendered, RenderRejected):
            return ServiceFailed(rendered.code, errors=rendered.errors)
        if not isinstance(rendered, RenderedImage):
            return ServiceFailed(SafeResultCode.IMAGE_INVALID)
        configuration = self._configuration_snapshot(normalized)
        request_fingerprint = self._operation_fingerprint(
            owner_identity_id=proof.identity_public_id,
            provider_id=proof.provider_id,
            command=command,
            configuration=configuration,
            image_digest=rendered.pixel_digest,
        )

        replay = self._replay_existing_operation(command.operation_id, request_fingerprint)
        if isinstance(replay, OperationResult):
            return replay

        resolved, failure = self._resolve_owner_channel(
            owner,
            PreviewRequest(
                channel_public_id=command.channel_public_id,
                expected_channel_revision=command.expected_channel_revision,
                template=normalized,
            ),
        )
        if failure is not None:
            return failure
        assert resolved is not None and resolved.snapshot is not None
        snapshot = resolved.snapshot
        if not snapshot.is_active:
            return ServiceFailed(SafeResultCode.CHANNEL_INACTIVE)
        scope = OwnerChannelScope(
            owner_identity_public_id=proof.identity_public_id,
            provider_id=proof.provider_id,
            channel_public_id=command.channel_public_id,
        )
        gateway_context = RichMenuGatewayContext(
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
            access_token=snapshot.access_token,
        )
        reconciliation = self._observe(scope, gateway_context)
        if isinstance(reconciliation, ServiceFailed):
            return reconciliation
        if reconciliation.observation.kind is ObservationKind.UNKNOWN:
            return ServiceFailed(SafeResultCode.OBSERVATION_UNKNOWN)
        expected_default_fingerprint = reconciliation.observation.fingerprint
        observation_failure = self._record_observation(
            scope, reconciliation.observation
        )
        if observation_failure is not None:
            return observation_failure
        scope_failure = self._verify_scope_unchanged(owner, snapshot)
        if scope_failure is not None:
            return scope_failure
        expected = PreviewSnapshot(
            owner_identity=proof.identity_public_id,
            provider_id=proof.provider_id,
            channel_public_id=command.channel_public_id,
            channel_revision=command.expected_channel_revision,
            default_observation_fingerprint=reconciliation.observation.fingerprint,
            template=normalized,
            pixel_digest=rendered.pixel_digest,
        )
        confirmation = self._confirmation.verify(
            command.confirmation_token,
            expected,
            self._clock(),
        )
        if isinstance(confirmation, ConfirmationRejected):
            return ServiceFailed(
                {
                    "preview_expired": SafeResultCode.PREVIEW_EXPIRED,
                    "preview_changed": SafeResultCode.TEMPLATE_CHANGED,
                    "preview_invalid": SafeResultCode.TEMPLATE_CHANGED,
                }.get(confirmation.reason, SafeResultCode.TEMPLATE_CHANGED)
            )
        if not isinstance(confirmation, ConfirmationAccepted):
            return ServiceFailed(SafeResultCode.TEMPLATE_CHANGED)

        accepted = self._repository.accept(
            AcceptedOperation(
                operation_id=command.operation_id,
                channel_public_id=command.channel_public_id,
                owner_identity_public_id=proof.identity_public_id,
                provider_id=proof.provider_id,
                expected_channel_revision=command.expected_channel_revision,
                kind=OperationKind.APPLY,
                subject_operation_id=None,
                target_resource_id=None,
                request_fingerprint=request_fingerprint,
                confirmation_usage_digest=confirmation.usage_digest,
                configuration_snapshot=configuration,
                candidate_image_digest=rendered.pixel_digest,
            )
        )
        if isinstance(accepted, OperationReplay):
            return OperationSucceeded(accepted.operation)
        if isinstance(accepted, OperationConflict):
            return self._map_operation_conflict(accepted.reason)
        if not isinstance(accepted, OperationAccepted):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if accepted.candidate_resource_id is None:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        candidate = self._get_candidate(scope, accepted.candidate_resource_id)
        if candidate is None:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        create_result = self._run_create_stage(
            owner=owner,
            scope=scope,
            operation_id=command.operation_id,
            candidate=candidate,
            snapshot=snapshot,
            normalized=normalized,
        )
        if isinstance(create_result, ServiceFailed):
            return create_result
        if not isinstance(create_result, OperationView):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if create_result.status is not OperationStatus.PROCESSING:
            return OperationSucceeded(create_result)
        candidate = self._get_candidate(scope, accepted.candidate_resource_id)
        if candidate is None or candidate.line_rich_menu_id is None:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        upload_result = self._run_upload_stage(
            owner=owner,
            scope=scope,
            operation_id=command.operation_id,
            candidate=candidate,
            snapshot=snapshot,
            image=rendered,
        )
        if isinstance(upload_result, ServiceFailed):
            return upload_result
        if upload_result.status is not OperationStatus.PROCESSING:
            return OperationSucceeded(upload_result)
        set_result = self._run_set_default_stage(
            owner=owner,
            scope=scope,
            operation_id=command.operation_id,
            candidate=candidate,
            snapshot=snapshot,
            expected_default_fingerprint=expected_default_fingerprint,
        )
        if isinstance(set_result, ServiceFailed):
            return set_result
        return OperationSucceeded(set_result)

    def _start_unlink(
        self, owner: OwnerOperationContext, command: OperationCommand
    ) -> OperationResult:
        if command.target_resource_id is None:
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        proof, snapshot, failure = self._resolve_operation_channel(owner, command)
        if failure is not None:
            return failure
        assert proof is not None and snapshot is not None
        if not snapshot.is_active:
            return ServiceFailed(SafeResultCode.CHANNEL_INACTIVE)
        scope = OwnerChannelScope(
            owner_identity_public_id=proof.identity_public_id,
            provider_id=proof.provider_id,
            channel_public_id=command.channel_public_id,
        )
        fingerprint = self._local_operation_fingerprint(proof, command)
        replay = self._replay_existing_operation(command.operation_id, fingerprint)
        if isinstance(replay, OperationResult):
            return replay
        target = self._get_candidate(scope, command.target_resource_id)
        if target is None or target.lifecycle is not ResourceLifecycle.APPLIED:
            return ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        context = self._gateway_context(snapshot)
        observed = self._observe(scope, context)
        if isinstance(observed, ServiceFailed):
            return observed
        accepted = self._accept_simple_operation(
            command=command,
            proof=proof,
            fingerprint=fingerprint,
        )
        if isinstance(accepted, OperationResult):
            return accepted
        if observed.observation.kind is ObservationKind.UNKNOWN:
            return self._start_unknown_simple_operation(
                operation_id=command.operation_id,
                stage=OperationStage.CLEARING_DEFAULT,
            )
        if observed.observation.managed_resource_id == target.public_id:
            result = self._run_unlink_clear_stage(
                owner=owner,
                operation_id=command.operation_id,
                scope=scope,
                snapshot=snapshot,
                target=target,
            )
        else:
            result = self._complete_unlink_without_clear(
                operation_id=command.operation_id,
                target=target,
            )
        if isinstance(result, ServiceFailed):
            return result
        return OperationSucceeded(self._with_unlink_actions(result))

    def _start_release(
        self, owner: OwnerOperationContext, command: OperationCommand
    ) -> OperationResult:
        if command.target_resource_id is None:
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        proof, snapshot, failure = self._resolve_operation_channel(owner, command)
        if failure is not None:
            return failure
        assert proof is not None and snapshot is not None
        if not snapshot.is_active:
            return ServiceFailed(SafeResultCode.CHANNEL_INACTIVE)
        scope = OwnerChannelScope(
            owner_identity_public_id=proof.identity_public_id,
            provider_id=proof.provider_id,
            channel_public_id=command.channel_public_id,
        )
        target = self._get_candidate(scope, command.target_resource_id)
        if target is None or target.lifecycle is not ResourceLifecycle.APPLIED:
            return ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        fingerprint = self._local_operation_fingerprint(proof, command)
        replay = self._replay_existing_operation(command.operation_id, fingerprint)
        if isinstance(replay, OperationResult):
            return replay
        accepted = self._accept_simple_operation(
            command=command,
            proof=proof,
            fingerprint=fingerprint,
        )
        if isinstance(accepted, OperationResult):
            return accepted
        claim = self._repository.claim_stage(
            command.operation_id, OperationStage.LOCAL_RELEASE
        )
        if isinstance(claim, StageExpired):
            return OperationSucceeded(claim.operation)
        if isinstance(claim, StageConflict):
            return self._map_stage_conflict(claim.reason)
        if not isinstance(claim, StageClaimed):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        released = self._finalize_release_resource(target.public_id)
        if released is not None:
            return released
        result = self._complete_stage(
            StageOutcome(
                operation_id=command.operation_id,
                expected_stage=OperationStage.LOCAL_RELEASE,
                next_status=OperationStatus.SUCCEEDED,
                next_stage=OperationStage.LOCAL_RELEASE,
                result=SafeResultCode.SUCCEEDED,
            )
        )
        if isinstance(result, ServiceFailed):
            return result
        return OperationSucceeded(result)

    def _start_recheck(
        self, owner: OwnerOperationContext, command: OperationCommand
    ) -> OperationResult:
        if command.subject_operation_id is None:
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        proof, snapshot, failure = self._resolve_operation_channel(owner, command)
        if failure is not None:
            return failure
        assert proof is not None and snapshot is not None
        if not snapshot.is_active:
            return ServiceFailed(SafeResultCode.CHANNEL_INACTIVE)
        scope = OwnerChannelScope(
            owner_identity_public_id=proof.identity_public_id,
            provider_id=proof.provider_id,
            channel_public_id=command.channel_public_id,
        )
        subject = self._get_operation(scope, command.subject_operation_id)
        if subject is None or subject.status is not OperationStatus.UNKNOWN:
            return ServiceFailed(SafeResultCode.OPERATION_CONFLICT)
        fingerprint = self._local_operation_fingerprint(proof, command)
        replay = self._replay_existing_operation(command.operation_id, fingerprint)
        if isinstance(replay, OperationResult):
            return replay
        accepted = self._accept_recovery_operation(
            command=command,
            proof=proof,
            fingerprint=fingerprint,
        )
        if isinstance(accepted, OperationResult):
            return accepted
        context = self._recheck_context(
            snapshot=snapshot,
            subject=subject,
            scope=scope,
            target_resource_id=subject.target_resource_id,
        )
        if isinstance(context, ServiceFailed):
            return context
        result = self._reconciler.recheck_operation(context)
        if isinstance(result, RecheckUnknown):
            handoff = self._complete_recovery_unknown(
                recovery_operation_id=command.operation_id,
                subject=subject,
                result_code=SafeResultCode.OBSERVATION_UNKNOWN,
            )
        elif isinstance(result, RecheckConfirmed):
            handoff = self._complete_recovery_confirmed(
                recovery_operation_id=command.operation_id,
                subject=subject,
                confirmation=result,
                target_resource_id=context.candidate.public_id
                if context.candidate is not None
                else context.target.public_id
                if context.target is not None
                else None,
            )
        else:
            return ServiceFailed(SafeResultCode.OBSERVATION_UNKNOWN)
        if isinstance(handoff, ServiceFailed):
            return handoff
        return OperationSucceeded(handoff)

    def _resolve_operation_channel(
        self, owner: OwnerOperationContext, command: OperationCommand
    ) -> tuple[OwnerActiveProof | None, RichMenuChannelSnapshot | None, ServiceFailed | None]:
        try:
            with transaction.atomic(using=self._using):
                proof = self._owner_fence.lock_active(owner, self._clock())
                if isinstance(proof, OwnerFenceFailed):
                    return None, None, self._map_owner_failure(proof.code)
                if not isinstance(proof, OwnerActiveProof):
                    return None, None, ServiceFailed(
                        SafeResultCode.OWNER_OPERATION_BLOCKED
                    )
                result = self._channel_port.snapshot_exact(
                    ChannelSnapshotCommand(
                        channel_public_id=command.channel_public_id,
                        owner_identity_public_id=proof.identity_public_id,
                        provider_id=proof.provider_id,
                        expected_channel_revision=command.expected_channel_revision,
                    )
                )
        except (TypeError, ValueError):
            return None, None, ServiceFailed(SafeResultCode.INVALID_INPUT)
        except Exception:
            return None, None, ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(result, ExactChannelSnapshotRejected):
            return None, None, self._map_snapshot_failure(result.code)
        snapshot = (
            result.snapshot
            if isinstance(result, ExactChannelSnapshotAvailable)
            else result
        )
        if not isinstance(snapshot, RichMenuChannelSnapshot):
            return None, None, ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        if (
            snapshot.owner_identity_public_id != proof.identity_public_id
            or snapshot.provider_id != proof.provider_id
            or snapshot.channel_public_id != command.channel_public_id
            or snapshot.channel_revision != command.expected_channel_revision
        ):
            return None, None, ServiceFailed(SafeResultCode.STALE_CHANNEL)
        return proof, snapshot, None

    @staticmethod
    def _gateway_context(snapshot: RichMenuChannelSnapshot) -> RichMenuGatewayContext:
        return RichMenuGatewayContext(
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
            access_token=snapshot.access_token,
        )

    @staticmethod
    def _local_operation_fingerprint(
        proof: OwnerActiveProof, command: OperationCommand
    ) -> str:
        payload = {
            "ownerIdentity": str(proof.identity_public_id),
            "providerId": proof.provider_id,
            "channelId": str(command.channel_public_id),
            "channelRevision": command.expected_channel_revision.isoformat(),
            "kind": command.kind.value,
            "subject": None
            if command.subject_operation_id is None
            else str(command.subject_operation_id),
            "target": None
            if command.target_resource_id is None
            else str(command.target_resource_id),
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    def _accept_simple_operation(
        self,
        *,
        command: OperationCommand,
        proof: OwnerActiveProof,
        fingerprint: str,
    ) -> OperationResult | None:
        try:
            accepted = self._repository.accept(
                AcceptedOperation(
                    operation_id=command.operation_id,
                    channel_public_id=command.channel_public_id,
                    owner_identity_public_id=proof.identity_public_id,
                    provider_id=proof.provider_id,
                    expected_channel_revision=command.expected_channel_revision,
                    kind=command.kind,
                    subject_operation_id=command.subject_operation_id,
                    target_resource_id=command.target_resource_id,
                    request_fingerprint=fingerprint,
                )
            )
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(accepted, OperationReplay):
            return OperationSucceeded(accepted.operation)
        if isinstance(accepted, OperationConflict):
            return self._map_operation_conflict(accepted.reason)
        if not isinstance(accepted, OperationAccepted):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return None

    def _accept_recovery_operation(
        self,
        *,
        command: OperationCommand,
        proof: OwnerActiveProof,
        fingerprint: str,
    ) -> OperationResult | None:
        accept_recovery = getattr(self._repository, "accept_recovery", None)
        if not callable(accept_recovery):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        try:
            accepted = accept_recovery(
                AcceptedOperation(
                    operation_id=command.operation_id,
                    channel_public_id=command.channel_public_id,
                    owner_identity_public_id=proof.identity_public_id,
                    provider_id=proof.provider_id,
                    expected_channel_revision=command.expected_channel_revision,
                    kind=command.kind,
                    subject_operation_id=command.subject_operation_id,
                    target_resource_id=command.target_resource_id,
                    request_fingerprint=fingerprint,
                )
            )
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(accepted, OperationReplay):
            return OperationSucceeded(accepted.operation)
        if isinstance(accepted, OperationConflict):
            return self._map_operation_conflict(accepted.reason)
        if not isinstance(accepted, RecoveryAccepted):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return None

    def _start_unknown_simple_operation(
        self, *, operation_id: UUID, stage: OperationStage
    ) -> OperationResult:
        claim = self._repository.claim_stage(operation_id, stage)
        if isinstance(claim, StageExpired):
            return OperationSucceeded(claim.operation)
        if isinstance(claim, StageConflict):
            return self._map_stage_conflict(claim.reason)
        if not isinstance(claim, StageClaimed):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        result = self._complete_unknown(
            operation_id, stage, SafeResultCode.OBSERVATION_UNKNOWN
        )
        if isinstance(result, ServiceFailed):
            return result
        return OperationSucceeded(result)

    def _get_operation(
        self, scope: OwnerChannelScope, operation_id: UUID
    ) -> OperationView | None:
        reader = getattr(self._repository, "get_operation", None)
        if callable(reader):
            try:
                result = reader(scope, operation_id)
            except TypeError:
                result = reader(
                    scope.owner_identity_public_id,
                    scope.provider_id,
                    operation_id,
                )
            if isinstance(result, OperationView):
                return result
        reader = getattr(self._repository, "get_operation_by_id", None)
        if callable(reader):
            try:
                result = reader(operation_id)
            except Exception:
                return None
            if isinstance(result, OperationView):
                return result
        return None

    def _recheck_context(
        self,
        *,
        snapshot: RichMenuChannelSnapshot,
        subject: OperationView,
        scope: OwnerChannelScope,
        target_resource_id: UUID | None,
    ) -> RecheckContext | ServiceFailed:
        candidate = None
        if subject.kind is OperationKind.APPLY:
            reader = getattr(self._repository, "get_candidate_for_operation", None)
            if callable(reader):
                try:
                    candidate = reader(scope, subject.operation_id)
                except TypeError:
                    candidate = reader(subject.operation_id)
            if candidate is None:
                try:
                    resources = self._repository.list_managed_resources(scope)
                except Exception:
                    resources = ()
                candidate = next(
                    (
                        resource
                        for resource in resources
                        if isinstance(resource, ManagedResourceTarget)
                        and resource.origin_operation_id == subject.operation_id
                    ),
                    None,
                )
        target = (
            self._get_candidate(scope, target_resource_id)
            if target_resource_id is not None
            else None
        )
        resource = candidate or target
        if subject.kind is OperationKind.APPLY and resource is None:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        image_digest = None
        image_reader = getattr(self._repository, "get_operation_image_digest", None)
        if callable(image_reader):
            try:
                image_digest = image_reader(subject.operation_id)
            except Exception:
                return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        try:
            return RecheckContext(
                gateway_context=self._gateway_context(snapshot),
                stage=subject.stage or OperationStage.VERIFYING,
                subject_operation_id=subject.operation_id,
                ownership_marker=None if resource is None else resource.ownership_marker,
                candidate=candidate,
                target=target,
                expected_image_digest=image_digest,
            )
        except ValueError:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)

    def _complete_recovery_unknown(
        self,
        *,
        recovery_operation_id: UUID,
        subject: OperationView,
        result_code: SafeResultCode,
    ) -> OperationView | ServiceFailed:
        completer = getattr(self._repository, "complete_recovery", None)
        if callable(completer):
            try:
                result = completer(
                    recovery_operation_id,
                    OperationStatus.UNKNOWN,
                    result_code,
                )
            except TypeError:
                result = completer(
                    recovery_operation_id=recovery_operation_id,
                    next_status=OperationStatus.UNKNOWN,
                    result=result_code,
                )
            except Exception:
                return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
            if isinstance(result, OperationView):
                return result
        if subject.kind is OperationKind.CLEANUP:
            handoff = getattr(self._repository, "handoff_recovery", None)
            if callable(handoff):
                result = handoff(
                    RecoveryOutcome(
                        recovery_operation_id=recovery_operation_id,
                        subject_operation_id=subject.operation_id,
                        subject_next_status=OperationStatus.CLEANUP_REQUIRED,
                        subject_next_stage=OperationStage.CLEANING,
                        subject_result=SafeResultCode.CLEANUP_REQUIRED,
                        blocker_moves_to_recovery=True,
                    )
                )
                if isinstance(result, RecoveryHandoffResult):
                    return result.recovery
        return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)

    def _complete_recovery_confirmed(
        self,
        *,
        recovery_operation_id: UUID,
        subject: OperationView,
        confirmation: RecheckConfirmed,
        target_resource_id: UUID | None,
    ) -> OperationView | ServiceFailed:
        if (
            confirmation.line_rich_menu_id is not None
            and target_resource_id is not None
        ):
            binder = getattr(self._repository, "bind_resource_line_id", None)
            if callable(binder):
                try:
                    bound = binder(target_resource_id, confirmation.line_rich_menu_id)
                except Exception:
                    return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
                if bound is False:
                    return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if subject.kind is OperationKind.CLEANUP:
            finalizer = getattr(self._repository, "complete_cleanup_recovery", None)
            if callable(finalizer):
                try:
                    result = finalizer(recovery_operation_id, target_resource_id)
                except Exception:
                    return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
                if isinstance(result, OperationView):
                    return result
        handoff = getattr(self._repository, "handoff_recovery", None)
        if not callable(handoff):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if subject.stage is OperationStage.VERIFYING:
            next_status = OperationStatus.SUCCEEDED
            next_stage = OperationStage.VERIFYING
            result_code = SafeResultCode.SUCCEEDED
        else:
            next_status = OperationStatus.PROCESSING
            next_stage = confirmation.next_stage or subject.stage
            result_code = SafeResultCode.ACCEPTED
        try:
            result = handoff(
                RecoveryOutcome(
                    recovery_operation_id=recovery_operation_id,
                    subject_operation_id=subject.operation_id,
                    subject_next_status=next_status,
                    subject_next_stage=next_stage,
                    subject_result=result_code,
                    blocker_moves_to_recovery=False,
                )
            )
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(result, RecoveryHandoffResult):
            return result.recovery
        if isinstance(result, StageConflict):
            return self._map_stage_conflict(result.reason)
        return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)

    def _complete_cleanup_recovery_success(
        self, *, recovery_operation_id: UUID, target_resource_id: UUID
    ) -> OperationView | ServiceFailed:
        completer = getattr(self._repository, "complete_cleanup_recovery", None)
        if not callable(completer):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        try:
            result = completer(recovery_operation_id, target_resource_id)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(result, OperationView):
            return result
        if isinstance(result, StageConflict):
            return self._map_stage_conflict(result.reason)
        return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)

    def _complete_unlink_without_clear(
        self, *, operation_id: UUID, target: ManagedResourceTarget
    ) -> OperationView | ServiceFailed:
        claim = self._repository.claim_stage(operation_id, OperationStage.CLEARING_DEFAULT)
        if isinstance(claim, StageExpired):
            return claim.operation
        if isinstance(claim, StageConflict):
            return self._map_stage_conflict(claim.reason)
        if not isinstance(claim, StageClaimed):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        finalized = self._finalize_unlink_resource(target.public_id)
        if finalized is not None:
            return finalized
        return self._complete_stage(
            StageOutcome(
                operation_id=operation_id,
                expected_stage=OperationStage.CLEARING_DEFAULT,
                next_status=OperationStatus.SUCCEEDED,
                next_stage=OperationStage.CLEARING_DEFAULT,
                result=SafeResultCode.NO_CHANGE,
            )
        )

    def _run_unlink_clear_stage(
        self,
        *,
        owner: OwnerOperationContext,
        operation_id: UUID,
        scope: OwnerChannelScope,
        snapshot: RichMenuChannelSnapshot,
        target: ManagedResourceTarget,
    ) -> OperationView | ServiceFailed:
        claim = self._repository.claim_stage(operation_id, OperationStage.CLEARING_DEFAULT)
        if isinstance(claim, StageExpired):
            return claim.operation
        if isinstance(claim, StageConflict):
            return self._map_stage_conflict(claim.reason)
        if not isinstance(claim, StageClaimed):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if not target.line_rich_menu_id:
            return self._complete_unknown(
                operation_id, OperationStage.CLEARING_DEFAULT, SafeResultCode.STORAGE_UNAVAILABLE
            )
        context = self._gateway_context(snapshot)
        try:
            result = self._gateway.clear_default(context)
        except Exception:
            return self._complete_unknown(
                operation_id,
                OperationStage.CLEARING_DEFAULT,
                SafeResultCode.RESPONSE_UNKNOWN,
            )
        if isinstance(result, GatewayRejected):
            return self._complete_stage(
                StageOutcome(
                    operation_id=operation_id,
                    expected_stage=OperationStage.CLEARING_DEFAULT,
                    next_status=OperationStatus.FAILED,
                    next_stage=OperationStage.CLEARING_DEFAULT,
                    result=self._gateway_rejection_code(result),
                )
            )
        if isinstance(result, GatewayUnknown):
            return self._complete_unknown(
                operation_id,
                OperationStage.CLEARING_DEFAULT,
                self._gateway_unknown_code(result),
            )
        if not isinstance(result, GatewayAccepted):
            return self._complete_unknown(
                operation_id,
                OperationStage.CLEARING_DEFAULT,
                SafeResultCode.RESPONSE_UNKNOWN,
            )
        scope_failure = self._verify_scope_unchanged(owner, snapshot)
        if scope_failure is not None:
            return self._complete_unknown(
                operation_id, OperationStage.CLEARING_DEFAULT, scope_failure.code
            )
        progressed = self._complete_stage(
            StageOutcome(
                operation_id=operation_id,
                expected_stage=OperationStage.CLEARING_DEFAULT,
                next_status=OperationStatus.PROCESSING,
                next_stage=OperationStage.VERIFYING,
                result=SafeResultCode.ACCEPTED,
            )
        )
        if isinstance(progressed, ServiceFailed):
            return progressed
        claim = self._repository.claim_stage(operation_id, OperationStage.VERIFYING)
        if isinstance(claim, StageExpired):
            return claim.operation
        if isinstance(claim, StageConflict):
            return self._map_stage_conflict(claim.reason)
        if not isinstance(claim, StageClaimed):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        observed = self._observe(scope, context)
        if isinstance(observed, ServiceFailed):
            return self._complete_unknown(
                operation_id, OperationStage.VERIFYING, observed.code
            )
        if observed.observation.kind is ObservationKind.UNKNOWN:
            return self._complete_unknown(
                operation_id, OperationStage.VERIFYING, SafeResultCode.OBSERVATION_UNKNOWN
            )
        if observed.observation.managed_resource_id == target.public_id:
            return self._complete_unknown(
                operation_id, OperationStage.VERIFYING, SafeResultCode.RESPONSE_UNKNOWN
            )
        finalized = self._finalize_unlink_resource(target.public_id)
        if finalized is not None:
            return self._complete_unknown(
                operation_id, OperationStage.VERIFYING, finalized.code
            )
        return self._complete_stage(
            StageOutcome(
                operation_id=operation_id,
                expected_stage=OperationStage.VERIFYING,
                next_status=OperationStatus.SUCCEEDED,
                next_stage=OperationStage.VERIFYING,
                result=SafeResultCode.SUCCEEDED,
            )
        )

    @staticmethod
    def _with_unlink_actions(operation: OperationView) -> OperationView:
        if operation.status is not OperationStatus.SUCCEEDED:
            return operation
        return operation.__class__(
            operation_id=operation.operation_id,
            kind=operation.kind,
            status=operation.status,
            stage=operation.stage,
            result=operation.result,
            subject_operation_id=operation.subject_operation_id,
            target_resource_id=operation.target_resource_id,
            accepted_at=operation.accepted_at,
            completed_at=operation.completed_at,
            next_allowed_actions=(
                NextAllowedAction.CLEAR_TO_DISABLE,
                NextAllowedAction.GET_STATE,
                NextAllowedAction.VIEW_HISTORY,
            ),
        )

    def _finalize_unlink_resource(self, resource_id: UUID) -> ServiceFailed | None:
        finalizer = getattr(self._repository, "finalize_unlink", None)
        if callable(finalizer):
            try:
                result = finalizer(resource_id)
            except Exception:
                return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
            if isinstance(result, OperationConflict):
                return self._map_operation_conflict(result.reason)
            if result is False:
                return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
            return None
        return self._mark_resource_cleanup_required(resource_id)

    def _finalize_release_resource(self, resource_id: UUID) -> ServiceFailed | None:
        finalizer = getattr(self._repository, "finalize_release", None)
        if not callable(finalizer):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        try:
            result = finalizer(resource_id)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(result, OperationConflict):
            return self._map_operation_conflict(result.reason)
        if result is False:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return None

    def _finalize_deleted_resource(self, resource_id: UUID) -> ServiceFailed | None:
        finalizer = getattr(self._repository, "finalize_deleted", None)
        if not callable(finalizer):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        try:
            result = finalizer(resource_id)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(result, OperationConflict):
            return self._map_operation_conflict(result.reason)
        if result is False:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return None

    def _start_cleanup(
        self, owner: OwnerOperationContext, command: OperationCommand
    ) -> OperationResult:
        if command.subject_operation_id is None or command.target_resource_id is None:
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        proof, snapshot, failure = self._resolve_operation_channel(owner, command)
        if failure is not None:
            return failure
        assert proof is not None and snapshot is not None
        if not snapshot.is_active:
            return ServiceFailed(SafeResultCode.CHANNEL_INACTIVE)
        scope = OwnerChannelScope(
            owner_identity_public_id=proof.identity_public_id,
            provider_id=proof.provider_id,
            channel_public_id=command.channel_public_id,
        )
        subject = self._get_operation(scope, command.subject_operation_id)
        target = self._get_candidate(scope, command.target_resource_id)
        if (
            subject is None
            or subject.status is not OperationStatus.CLEANUP_REQUIRED
            or target is None
            or target.lifecycle
            not in {
                ResourceLifecycle.CANDIDATE,
                ResourceLifecycle.OLD,
                ResourceLifecycle.CLEANUP_REQUIRED,
            }
            or not (
                target.origin_operation_id == subject.operation_id
                or target.replacement_operation_id == subject.operation_id
            )
        ):
            return ServiceFailed(SafeResultCode.OPERATION_CONFLICT)
        fingerprint = self._local_operation_fingerprint(proof, command)
        replay = self._replay_existing_operation(command.operation_id, fingerprint)
        if isinstance(replay, OperationResult):
            return replay
        accepted = self._accept_recovery_operation(
            command=command,
            proof=proof,
            fingerprint=fingerprint,
        )
        if isinstance(accepted, OperationResult):
            return accepted
        context = self._recheck_context(
            snapshot=snapshot,
            subject=subject,
            scope=scope,
            target_resource_id=target.public_id,
        )
        if isinstance(context, ServiceFailed):
            return context
        precheck = self._reconciler.recheck_operation(context)
        if isinstance(precheck, RecheckUnknown):
            handoff = self._complete_recovery_unknown(
                recovery_operation_id=command.operation_id,
                subject=subject,
                result_code=SafeResultCode.OBSERVATION_UNKNOWN,
            )
            if isinstance(handoff, ServiceFailed):
                return handoff
            return OperationSucceeded(handoff)
        if not isinstance(precheck, RecheckConfirmed):
            return ServiceFailed(SafeResultCode.OBSERVATION_UNKNOWN)
        if target.line_rich_menu_id is None:
            return self._complete_recovery_unknown(
                recovery_operation_id=command.operation_id,
                subject=subject,
                result_code=SafeResultCode.STORAGE_UNAVAILABLE,
            )
        observed = self._observe(scope, self._gateway_context(snapshot))
        if isinstance(observed, ServiceFailed):
            return observed
        if observed.observation.kind is ObservationKind.UNKNOWN or (
            observed.observation.managed_resource_id == target.public_id
        ):
            handoff = self._complete_recovery_unknown(
                recovery_operation_id=command.operation_id,
                subject=subject,
                result_code=SafeResultCode.OBSERVATION_UNKNOWN
                if observed.observation.kind is ObservationKind.UNKNOWN
                else SafeResultCode.CLEANUP_REQUIRED,
            )
            if isinstance(handoff, ServiceFailed):
                return handoff
            return OperationSucceeded(handoff)
        try:
            delete_result = self._gateway.delete(
                self._gateway_context(snapshot), target.line_rich_menu_id
            )
        except Exception:
            delete_result = GatewayUnknown("response_unknown")
        if isinstance(delete_result, GatewayUnknown):
            handoff = self._complete_recovery_unknown(
                recovery_operation_id=command.operation_id,
                subject=subject,
                result_code=self._gateway_unknown_code(delete_result),
            )
        elif isinstance(delete_result, GatewayRejected):
            handoff = self._complete_recovery_unknown(
                recovery_operation_id=command.operation_id,
                subject=subject,
                result_code=self._gateway_rejection_code(delete_result),
            )
        elif isinstance(delete_result, GatewayAccepted):
            scope_failure = self._verify_scope_unchanged(owner, snapshot)
            if scope_failure is not None:
                handoff = self._complete_recovery_unknown(
                    recovery_operation_id=command.operation_id,
                    subject=subject,
                    result_code=scope_failure.code,
                )
                if isinstance(handoff, ServiceFailed):
                    return handoff
                return OperationSucceeded(handoff)
            deleted = self._finalize_deleted_resource(target.public_id)
            if deleted is not None:
                handoff = self._complete_recovery_unknown(
                    recovery_operation_id=command.operation_id,
                    subject=subject,
                    result_code=deleted.code,
                )
                if isinstance(handoff, ServiceFailed):
                    return handoff
                return OperationSucceeded(handoff)
            handoff = self._complete_cleanup_recovery_success(
                recovery_operation_id=command.operation_id,
                target_resource_id=target.public_id,
            )
        else:
            handoff = self._complete_recovery_unknown(
                recovery_operation_id=command.operation_id,
                subject=subject,
                result_code=SafeResultCode.RESPONSE_UNKNOWN,
            )
        if isinstance(handoff, ServiceFailed):
            return handoff
        return OperationSucceeded(handoff)

    def _replay_existing_operation(
        self, operation_id: UUID, request_fingerprint: str
    ) -> OperationResult | None:
        lookup = getattr(self._repository, "get_operation_by_id", None)
        fingerprint_reader = getattr(self._repository, "get_request_fingerprint", None)
        if not callable(lookup) or not callable(fingerprint_reader):
            return None
        try:
            existing = lookup(operation_id)
            if existing is None:
                return None
            stored_fingerprint = fingerprint_reader(operation_id)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if stored_fingerprint != request_fingerprint:
            return ServiceFailed(SafeResultCode.OPERATION_CONFLICT)
        if not isinstance(existing, OperationView):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return OperationSucceeded(existing)

    def _get_candidate(
        self, scope: OwnerChannelScope, resource_id: UUID
    ) -> ManagedResourceTarget | None:
        reader = getattr(self._repository, "get_managed_resource", None)
        if not callable(reader):
            reader = getattr(self._repository, "get_candidate", None)
        if not callable(reader):
            return None
        try:
            candidate = reader(scope, resource_id)
        except TypeError:
            candidate = reader(resource_id)
        return candidate if isinstance(candidate, ManagedResourceTarget) else None

    def _run_create_stage(
        self,
        *,
        owner: OwnerOperationContext,
        scope: OwnerChannelScope,
        operation_id: UUID,
        candidate: ManagedResourceTarget,
        snapshot: RichMenuChannelSnapshot,
        normalized: NormalizedTemplate,
    ) -> OperationView | ServiceFailed:
        claim = self._repository.claim_stage(operation_id, OperationStage.CREATING)
        if isinstance(claim, StageExpired):
            return claim.operation
        if isinstance(claim, StageConflict):
            return self._map_stage_conflict(claim.reason)
        if not isinstance(claim, StageClaimed):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        context = RichMenuGatewayContext(
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
            access_token=snapshot.access_token,
        )
        request = self._build_rich_menu_object(
            normalized, name=candidate.ownership_marker or "lrm:v1:unknown"
        )
        try:
            result = self._gateway.create(context, request)
        except Exception:
            return self._complete_unknown(
                operation_id, OperationStage.CREATING, SafeResultCode.RESPONSE_UNKNOWN
            )
        if isinstance(result, CreateAccepted):
            scope_failure = self._verify_scope_unchanged(owner, snapshot)
            if scope_failure is not None:
                return self._complete_unknown(
                    operation_id, OperationStage.CREATING, scope_failure.code
                )
            binder = getattr(self._repository, "bind_resource_line_id", None)
            if not callable(binder):
                return self._complete_unknown(
                    operation_id, OperationStage.CREATING, SafeResultCode.STORAGE_UNAVAILABLE
                )
            try:
                bound = binder(candidate.public_id, result.line_rich_menu_id)
            except Exception:
                bound = False
            if bound is False:
                return self._complete_unknown(
                    operation_id, OperationStage.CREATING, SafeResultCode.STORAGE_UNAVAILABLE
                )
            return self._complete_stage(
                StageOutcome(
                    operation_id=operation_id,
                    expected_stage=OperationStage.CREATING,
                    next_status=OperationStatus.PROCESSING,
                    next_stage=OperationStage.UPLOADING,
                    result=SafeResultCode.ACCEPTED,
                )
            )
        if isinstance(result, GatewayRejected):
            discarded = self._discard_candidate(candidate.public_id)
            if discarded is not None:
                return discarded
            return self._complete_stage(
                StageOutcome(
                    operation_id=operation_id,
                    expected_stage=OperationStage.CREATING,
                    next_status=OperationStatus.FAILED,
                    next_stage=OperationStage.CREATING,
                    result=self._gateway_rejection_code(result),
                )
            )
        return self._complete_stage(
            StageOutcome(
                operation_id=operation_id,
                expected_stage=OperationStage.CREATING,
                next_status=OperationStatus.UNKNOWN,
                next_stage=OperationStage.CREATING,
                result=self._gateway_unknown_code(result),
            )
        )

    def _run_upload_stage(
        self,
        *,
        owner: OwnerOperationContext,
        scope: OwnerChannelScope,
        operation_id: UUID,
        candidate: ManagedResourceTarget,
        snapshot: RichMenuChannelSnapshot,
        image: RenderedImage,
    ) -> OperationView | ServiceFailed:
        claim = self._repository.claim_stage(operation_id, OperationStage.UPLOADING)
        if isinstance(claim, StageExpired):
            return claim.operation
        if isinstance(claim, StageConflict):
            return self._map_stage_conflict(claim.reason)
        if not isinstance(claim, StageClaimed):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if not candidate.line_rich_menu_id:
            return self._complete_unknown(
                operation_id, OperationStage.UPLOADING, SafeResultCode.STORAGE_UNAVAILABLE
            )
        context = RichMenuGatewayContext(
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
            access_token=snapshot.access_token,
        )
        try:
            result = self._gateway.upload(
                context, candidate.line_rich_menu_id, image
            )
        except Exception:
            return self._complete_unknown(
                operation_id, OperationStage.UPLOADING, SafeResultCode.RESPONSE_UNKNOWN
            )
        if isinstance(result, GatewayAccepted):
            scope_failure = self._verify_scope_unchanged(owner, snapshot)
            if scope_failure is not None:
                return self._complete_unknown(
                    operation_id,
                    OperationStage.UPLOADING,
                    scope_failure.code,
                )
            return self._complete_stage(
                StageOutcome(
                    operation_id=operation_id,
                    expected_stage=OperationStage.UPLOADING,
                    next_status=OperationStatus.PROCESSING,
                    next_stage=OperationStage.SETTING_DEFAULT,
                    result=SafeResultCode.ACCEPTED,
                )
            )
        if isinstance(result, GatewayRejected):
            cleanup_failure = self._mark_resource_cleanup_required(candidate.public_id)
            if cleanup_failure is not None:
                return cleanup_failure
            return self._complete_cleanup_required(
                operation_id,
                self._gateway_rejection_code(result),
            )
        return self._complete_unknown(
            operation_id,
            OperationStage.UPLOADING,
            self._gateway_unknown_code(result),
        )

    def _run_set_default_stage(
        self,
        *,
        owner: OwnerOperationContext,
        scope: OwnerChannelScope,
        operation_id: UUID,
        candidate: ManagedResourceTarget,
        snapshot: RichMenuChannelSnapshot,
        expected_default_fingerprint: str,
    ) -> OperationView | ServiceFailed:
        claim = self._repository.claim_stage(
            operation_id, OperationStage.SETTING_DEFAULT
        )
        if isinstance(claim, StageExpired):
            return claim.operation
        if isinstance(claim, StageConflict):
            return self._map_stage_conflict(claim.reason)
        if not isinstance(claim, StageClaimed):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if not candidate.line_rich_menu_id:
            return self._complete_unknown(
                operation_id,
                OperationStage.SETTING_DEFAULT,
                SafeResultCode.STORAGE_UNAVAILABLE,
            )

        context = RichMenuGatewayContext(
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
            access_token=snapshot.access_token,
        )
        before_set = self._observe(scope, context)
        if isinstance(before_set, ServiceFailed):
            return self._complete_unknown(
                operation_id,
                OperationStage.SETTING_DEFAULT,
                before_set.code,
            )
        if before_set.observation.kind is ObservationKind.UNKNOWN:
            return self._complete_unknown(
                operation_id,
                OperationStage.SETTING_DEFAULT,
                SafeResultCode.OBSERVATION_UNKNOWN,
            )
        if before_set.observation.fingerprint != expected_default_fingerprint:
            cleanup_failure = self._mark_resource_cleanup_required(candidate.public_id)
            if cleanup_failure is not None:
                return cleanup_failure
            return self._complete_cleanup_required(
                operation_id,
                SafeResultCode.CLEANUP_REQUIRED,
                expected_stage=OperationStage.SETTING_DEFAULT,
            )
        scope_failure = self._verify_scope_unchanged(owner, snapshot)
        if scope_failure is not None:
            return self._complete_unknown(
                operation_id,
                OperationStage.SETTING_DEFAULT,
                scope_failure.code,
            )

        try:
            result = self._gateway.set_default(context, candidate.line_rich_menu_id)
        except Exception:
            return self._complete_unknown(
                operation_id,
                OperationStage.SETTING_DEFAULT,
                SafeResultCode.RESPONSE_UNKNOWN,
            )
        if isinstance(result, GatewayRejected):
            cleanup_failure = self._mark_resource_cleanup_required(candidate.public_id)
            if cleanup_failure is not None:
                return cleanup_failure
            return self._complete_cleanup_required(
                operation_id,
                self._gateway_rejection_code(result),
                expected_stage=OperationStage.SETTING_DEFAULT,
            )
        if isinstance(result, GatewayUnknown):
            return self._complete_unknown(
                operation_id,
                OperationStage.SETTING_DEFAULT,
                self._gateway_unknown_code(result),
            )
        if not isinstance(result, GatewayAccepted):
            return self._complete_unknown(
                operation_id,
                OperationStage.SETTING_DEFAULT,
                SafeResultCode.RESPONSE_UNKNOWN,
            )
        scope_failure = self._verify_scope_unchanged(owner, snapshot)
        if scope_failure is not None:
            return self._complete_unknown(
                operation_id,
                OperationStage.SETTING_DEFAULT,
                scope_failure.code,
            )
        progressed = self._complete_stage(
            StageOutcome(
                operation_id=operation_id,
                expected_stage=OperationStage.SETTING_DEFAULT,
                next_status=OperationStatus.PROCESSING,
                next_stage=OperationStage.VERIFYING,
                result=SafeResultCode.ACCEPTED,
            )
        )
        if isinstance(progressed, ServiceFailed):
            return progressed
        return self._run_verify_stage(
            owner=owner,
            scope=scope,
            operation_id=operation_id,
            candidate=candidate,
            snapshot=snapshot,
        )

    def _run_verify_stage(
        self,
        *,
        owner: OwnerOperationContext,
        scope: OwnerChannelScope,
        operation_id: UUID,
        candidate: ManagedResourceTarget,
        snapshot: RichMenuChannelSnapshot,
    ) -> OperationView | ServiceFailed:
        claim = self._repository.claim_stage(operation_id, OperationStage.VERIFYING)
        if isinstance(claim, StageExpired):
            return claim.operation
        if isinstance(claim, StageConflict):
            return self._map_stage_conflict(claim.reason)
        if not isinstance(claim, StageClaimed):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        context = RichMenuGatewayContext(
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
            access_token=snapshot.access_token,
        )
        observed = self._observe(scope, context)
        if isinstance(observed, ServiceFailed):
            return self._complete_unknown(
                operation_id, OperationStage.VERIFYING, observed.code
            )
        if observed.observation.kind is ObservationKind.UNKNOWN:
            return self._complete_unknown(
                operation_id,
                OperationStage.VERIFYING,
                SafeResultCode.OBSERVATION_UNKNOWN,
            )
        if observed.observation.managed_resource_id != candidate.public_id:
            cleanup_failure = self._mark_resource_cleanup_required(candidate.public_id)
            if cleanup_failure is not None:
                return cleanup_failure
            return self._complete_cleanup_required(
                operation_id,
                SafeResultCode.CLEANUP_REQUIRED,
                expected_stage=OperationStage.VERIFYING,
            )
        scope_failure = self._verify_scope_unchanged(owner, snapshot)
        if scope_failure is not None:
            return self._complete_unknown(
                operation_id, OperationStage.VERIFYING, scope_failure.code
            )
        finalized = self._finalize_apply_resources(operation_id, candidate.public_id)
        if isinstance(finalized, ServiceFailed):
            return self._complete_unknown(
                operation_id, OperationStage.VERIFYING, finalized.code
            )
        return self._complete_stage(
            StageOutcome(
                operation_id=operation_id,
                expected_stage=OperationStage.VERIFYING,
                next_status=OperationStatus.SUCCEEDED,
                next_stage=OperationStage.VERIFYING,
                result=SafeResultCode.SUCCEEDED,
            )
        )

    def _complete_stage(self, outcome: StageOutcome) -> OperationView | ServiceFailed:
        try:
            result = self._repository.complete_stage(outcome)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(result, StageConflict):
            return self._map_stage_conflict(result.reason)
        if not isinstance(result, OperationView):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return result

    def _mark_resource_cleanup_required(self, resource_id: UUID) -> ServiceFailed | None:
        marker = getattr(self._repository, "mark_resource_cleanup_required", None)
        if not callable(marker):
            return None
        try:
            result = marker(resource_id)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(result, OperationConflict):
            return self._map_operation_conflict(result.reason)
        if result is False:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return None

    def _discard_candidate(self, resource_id: UUID) -> ServiceFailed | None:
        discarder = getattr(self._repository, "discard_candidate", None)
        if not callable(discarder):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        try:
            result = discarder(resource_id)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(result, OperationConflict):
            return self._map_operation_conflict(result.reason)
        if result is False:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return None

    def _finalize_apply_resources(
        self, operation_id: UUID, candidate_resource_id: UUID
    ) -> ServiceFailed | None:
        finalizer = getattr(self._repository, "finalize_apply", None)
        if callable(finalizer):
            try:
                result = finalizer(operation_id, candidate_resource_id)
            except TypeError:
                try:
                    result = finalizer(
                        operation_id=operation_id,
                        candidate_resource_id=candidate_resource_id,
                    )
                except Exception:
                    return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
            except Exception:
                return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
            if isinstance(result, OperationConflict):
                return self._map_operation_conflict(result.reason)
            if result is False:
                return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
            return None
        marker = getattr(self._repository, "mark_resource_applied", None)
        if not callable(marker):
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        try:
            result = marker(candidate_resource_id)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if result is False:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return None

    def _complete_unknown(
        self, operation_id: UUID, stage: OperationStage, code: SafeResultCode
    ) -> OperationView | ServiceFailed:
        return self._complete_stage(
            StageOutcome(
                operation_id=operation_id,
                expected_stage=stage,
                next_status=OperationStatus.UNKNOWN,
                next_stage=stage,
                result=code
                if code
                in {
                    SafeResultCode.TIMEOUT_UNKNOWN,
                    SafeResultCode.RESPONSE_UNKNOWN,
                    SafeResultCode.RATE_LIMITED,
                    SafeResultCode.STALE_CHANNEL,
                    SafeResultCode.STORAGE_UNAVAILABLE,
                    SafeResultCode.STORAGE_RETRYABLE,
                }
                else SafeResultCode.RESPONSE_UNKNOWN,
            )
        )

    def _complete_cleanup_required(
        self,
        operation_id: UUID,
        code: SafeResultCode,
        *,
        expected_stage: OperationStage = OperationStage.UPLOADING,
    ) -> OperationView | ServiceFailed:
        result = code
        if result not in {
            SafeResultCode.LINE_REJECTED,
            SafeResultCode.TIMEOUT_UNKNOWN,
            SafeResultCode.RESPONSE_UNKNOWN,
            SafeResultCode.RATE_LIMITED,
            SafeResultCode.STALE_CHANNEL,
            SafeResultCode.STORAGE_UNAVAILABLE,
            SafeResultCode.STORAGE_RETRYABLE,
        }:
            result = SafeResultCode.CLEANUP_REQUIRED
        return self._complete_stage(
            StageOutcome(
                operation_id=operation_id,
                expected_stage=expected_stage,
                next_status=OperationStatus.CLEANUP_REQUIRED,
                next_stage=OperationStage.CLEANING,
                result=result,
            )
        )

    @staticmethod
    def _configuration_snapshot(template: NormalizedTemplate) -> dict[str, object]:
        return {
            "templateId": template.reference.template_id,
            "templateVersion": template.reference.version,
            "fields": [
                {"displayName": field.display_name, "uri": field.uri}
                for field in template.fields
            ],
        }

    @staticmethod
    def _operation_fingerprint(
        *,
        owner_identity_id: UUID,
        provider_id: str,
        command: OperationCommand,
        configuration: dict[str, object],
        image_digest: str,
    ) -> str:
        payload = {
            "ownerIdentity": str(owner_identity_id),
            "providerId": provider_id,
            "channelId": str(command.channel_public_id),
            "channelRevision": command.expected_channel_revision.isoformat(),
            "kind": command.kind.value,
            "subject": None
            if command.subject_operation_id is None
            else str(command.subject_operation_id),
            "target": None
            if command.target_resource_id is None
            else str(command.target_resource_id),
            "configuration": configuration,
            "imageDigest": image_digest,
            "confirmationDigest": sha256(
                (command.confirmation_token or "").encode("utf-8")
            ).hexdigest(),
        }
        return sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    @staticmethod
    def _map_operation_conflict(reason: str) -> ServiceFailed:
        mapping = {
            "operation_conflict": SafeResultCode.OPERATION_CONFLICT,
            "operation_in_progress": SafeResultCode.OPERATION_IN_PROGRESS,
            "confirmation_used": SafeResultCode.OPERATION_CONFLICT,
            "channel_unavailable": SafeResultCode.CHANNEL_UNAVAILABLE,
            "storage_retryable": SafeResultCode.STORAGE_RETRYABLE,
            "storage_unavailable": SafeResultCode.STORAGE_UNAVAILABLE,
            "stale_channel": SafeResultCode.STALE_CHANNEL,
            "invalid_relation": SafeResultCode.OPERATION_CONFLICT,
        }
        return ServiceFailed(mapping.get(reason, SafeResultCode.OPERATION_CONFLICT))

    @staticmethod
    def _map_stage_conflict(reason: str) -> ServiceFailed:
        mapping = {
            "operation_not_found": SafeResultCode.CHANNEL_UNAVAILABLE,
            "stage_in_flight": SafeResultCode.OPERATION_IN_PROGRESS,
            "stale_stage": SafeResultCode.OPERATION_CONFLICT,
            "invalid_transition": SafeResultCode.OPERATION_CONFLICT,
        }
        return ServiceFailed(mapping.get(reason, SafeResultCode.OPERATION_CONFLICT))

    @staticmethod
    def _gateway_rejection_code(result: GatewayRejected) -> SafeResultCode:
        return {
            "line_rejected": SafeResultCode.LINE_REJECTED,
            "invalid_input": SafeResultCode.INVALID_INPUT,
            "image_invalid": SafeResultCode.IMAGE_INVALID,
        }.get(result.code, SafeResultCode.LINE_REJECTED)

    @staticmethod
    def _gateway_unknown_code(result) -> SafeResultCode:
        return {
            "timeout_unknown": SafeResultCode.TIMEOUT_UNKNOWN,
            "response_unknown": SafeResultCode.RESPONSE_UNKNOWN,
            "rate_limited": SafeResultCode.RATE_LIMITED,
        }.get(getattr(result, "code", "response_unknown"), SafeResultCode.RESPONSE_UNKNOWN)

    def _lock_owner(
        self, owner: OwnerOperationContext
    ) -> tuple[OwnerActiveProof | None, ServiceFailed | None]:
        if not isinstance(owner, OwnerOperationContext):
            return None, ServiceFailed(SafeResultCode.AUTHENTICATION_REQUIRED)
        try:
            with transaction.atomic(using=self._using):
                proof = self._owner_fence.lock_active(owner, self._clock())
        except Exception:
            return None, ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if isinstance(proof, OwnerFenceFailed):
            return None, self._map_owner_failure(proof.code)
        if not isinstance(proof, OwnerActiveProof):
            return None, ServiceFailed(SafeResultCode.OWNER_OPERATION_BLOCKED)
        return proof, None

    def _resolve_state_channel(
        self,
        owner: OwnerOperationContext,
        channel_id: UUID,
        *,
        expected_channel_revision: datetime | None,
    ) -> tuple[_ResolvedOwnerChannel | None, bool, ServiceFailed | None]:
        proof, failure = self._lock_owner(owner)
        if failure is not None:
            return None, False, failure
        assert proof is not None
        reader = getattr(self._channel_port, "snapshot_latest", None)
        result = None
        inactive = False
        try:
            with transaction.atomic(using=self._using):
                if callable(reader):
                    try:
                        result = reader(channel_id, proof.identity_public_id, proof.provider_id)
                    except TypeError:
                        result = reader(
                            channel_public_id=channel_id,
                            owner_identity_public_id=proof.identity_public_id,
                            provider_id=proof.provider_id,
                        )
                else:
                    metadata_reader = getattr(
                        self._channel_port, "get_for_owner_provider", None
                    )
                    metadata = (
                        metadata_reader(channel_id, proof.provider_id)
                        if callable(metadata_reader)
                        else None
                    )
                    if metadata is None and callable(metadata_reader):
                        return None, False, ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
                    if metadata is not None:
                        if not getattr(metadata, "is_active", False):
                            inactive = True
                        else:
                            expected = expected_channel_revision or getattr(
                                metadata, "updated_at", None
                            )
                            if not isinstance(expected, datetime):
                                return None, False, ServiceFailed(SafeResultCode.STALE_CHANNEL)
                            result = self._channel_port.snapshot_exact(
                                ChannelSnapshotCommand(
                                    channel_public_id=channel_id,
                                    owner_identity_public_id=proof.identity_public_id,
                                    provider_id=proof.provider_id,
                                    expected_channel_revision=expected,
                                )
                            )
                    elif expected_channel_revision is not None:
                        result = self._channel_port.snapshot_exact(
                            ChannelSnapshotCommand(
                                channel_public_id=channel_id,
                                owner_identity_public_id=proof.identity_public_id,
                                provider_id=proof.provider_id,
                                expected_channel_revision=expected_channel_revision,
                            )
                        )
        except (TypeError, ValueError):
            return None, False, ServiceFailed(SafeResultCode.INVALID_INPUT)
        except Exception:
            return None, False, ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)

        if inactive:
            return _ResolvedOwnerChannel(proof=proof, snapshot=None), True, None
        if isinstance(result, ExactChannelSnapshotRejected):
            if result.code == "channel_inactive":
                return _ResolvedOwnerChannel(proof=proof, snapshot=None), True, None
            return None, False, self._map_snapshot_failure(result.code)
        snapshot = (
            result.snapshot
            if isinstance(result, ExactChannelSnapshotAvailable)
            else result
        )
        if snapshot is None:
            return _ResolvedOwnerChannel(proof=proof, snapshot=None), False, None
        if not isinstance(snapshot, RichMenuChannelSnapshot):
            return None, False, ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        if (
            snapshot.owner_identity_public_id != proof.identity_public_id
            or snapshot.provider_id != proof.provider_id
            or snapshot.channel_public_id != channel_id
        ):
            return None, False, ServiceFailed(SafeResultCode.STALE_CHANNEL)
        if not snapshot.is_active:
            return _ResolvedOwnerChannel(proof=proof, snapshot=snapshot), True, None
        return _ResolvedOwnerChannel(proof=proof, snapshot=snapshot), False, None

    @staticmethod
    def _empty_state(channel_id: UUID) -> ChannelStateView:
        return ChannelStateView(
            channel_public_id=channel_id,
            current_resource=None,
            blocking_operation=None,
            active_operation=None,
            cleanup_resources=(),
            latest_observation=None,
            history_summary=HistorySummary(
                total_count=0,
                latest_operation_id=None,
                latest_status=None,
            ),
            next_allowed_actions=(
                NextAllowedAction.APPLY,
                NextAllowedAction.GET_STATE,
                NextAllowedAction.VIEW_HISTORY,
            ),
        )

    def _coerce_preview_request(
        self, command: PreviewRequest | PreviewCommand
    ) -> PreviewRequest | None:
        if isinstance(command, PreviewRequest):
            return command
        if isinstance(command, PreviewCommand):
            return PreviewRequest(
                channel_public_id=command.channel_public_id,
                expected_channel_revision=command.expected_channel_revision,
                template=command.template,
            )
        return None

    def _resolve_owner_channel(
        self, owner: OwnerOperationContext, request: PreviewRequest
    ) -> tuple[_ResolvedOwnerChannel | None, ServiceFailed | None]:
        try:
            with transaction.atomic(using=self._using):
                proof = self._owner_fence.lock_active(owner, self._clock())
                if isinstance(proof, OwnerFenceFailed):
                    return None, self._map_owner_failure(proof.code)
                if not isinstance(proof, OwnerActiveProof):
                    return None, ServiceFailed(SafeResultCode.OWNER_OPERATION_BLOCKED)
                result = self._channel_port.snapshot_exact(
                    ChannelSnapshotCommand(
                        channel_public_id=request.channel_public_id,
                        owner_identity_public_id=proof.identity_public_id,
                        provider_id=proof.provider_id,
                        expected_channel_revision=request.expected_channel_revision,
                    )
                )
        except (TypeError, ValueError):
            return None, ServiceFailed(SafeResultCode.INVALID_INPUT)
        except Exception:
            return None, ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)

        if isinstance(result, ExactChannelSnapshotRejected):
            return None, self._map_snapshot_failure(result.code)
        snapshot = (
            result.snapshot
            if isinstance(result, ExactChannelSnapshotAvailable)
            else result
        )
        if not isinstance(snapshot, RichMenuChannelSnapshot):
            return None, ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        if (
            snapshot.owner_identity_public_id != proof.identity_public_id
            or snapshot.provider_id != proof.provider_id
            or snapshot.channel_public_id != request.channel_public_id
            or snapshot.channel_revision != request.expected_channel_revision
        ):
            return None, ServiceFailed(SafeResultCode.STALE_CHANNEL)
        return _ResolvedOwnerChannel(proof=proof, snapshot=snapshot), None

    def _normalize_template(
        self, template: TemplateInput | NormalizedTemplate
    ) -> tuple[NormalizedTemplate | None, ServiceFailed | None]:
        if isinstance(template, NormalizedTemplate):
            return template, None
        if not isinstance(template, TemplateInput):
            return None, ServiceFailed(SafeResultCode.INVALID_INPUT)
        result = self._catalog.normalize(template)
        if isinstance(result, InputRejected):
            return None, ServiceFailed(
                SafeResultCode.INVALID_INPUT,
                errors=result.errors,
            )
        if not isinstance(result, NormalizedTemplate):
            return None, ServiceFailed(SafeResultCode.INVALID_INPUT)
        return result, None

    def _build_rich_menu_object(
        self, template: NormalizedTemplate, *, name: str = "lrm:v1:preview"
    ) -> RichMenuObject:
        descriptor = self._catalog.get(template.reference)
        if descriptor is None or len(descriptor.areas) != len(template.fields):
            raise ValueError("template descriptor unavailable")
        return RichMenuObject(
            width=descriptor.width,
            height=descriptor.height,
            name=name,
            chat_bar_text="メニュー",
            areas=tuple(
                RichMenuArea(
                    bounds=RichMenuBounds(
                        x=area.x,
                        y=area.y,
                        width=area.width,
                        height=area.height,
                    ),
                    action=RichMenuUriAction(field.uri),
                )
                for area, field in zip(descriptor.areas, template.fields, strict=True)
            ),
        )

    def _observe(
        self, scope: OwnerChannelScope, gateway_context: RichMenuGatewayContext
    ) -> Reconciliation | ServiceFailed:
        try:
            resources = self._repository.list_managed_resources(scope)
            if not isinstance(resources, tuple):
                resources = tuple(resources)
            current = next(
                (
                    resource
                    for resource in resources
                    if resource.lifecycle.value == "applied"
                ),
                None,
            )
            result = self._reconciler.observe_channel(
                ReconcileContext(
                    gateway_context=gateway_context,
                    current_resource=current,
                    managed_resources=resources,
                )
            )
        except (TypeError, ValueError):
            return ServiceFailed(SafeResultCode.OBSERVATION_UNKNOWN)
        except Exception:
            return ServiceFailed(SafeResultCode.OBSERVATION_UNKNOWN)
        if not isinstance(result, Reconciliation):
            return ServiceFailed(SafeResultCode.OBSERVATION_UNKNOWN)
        return result

    def _record_observation(
        self, scope: OwnerChannelScope, observation: DefaultObservation
    ) -> ServiceFailed | None:
        recorder = getattr(self._repository, "record_observation", None)
        if not callable(recorder):
            return None
        try:
            result = recorder(scope, observation)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        if result is False:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return None

    def _verify_scope_unchanged(
        self, owner: OwnerOperationContext, snapshot: RichMenuChannelSnapshot
    ) -> ServiceFailed | None:
        try:
            with transaction.atomic(using=self._using):
                proof = self._owner_fence.lock_active(owner, self._clock())
                if isinstance(proof, OwnerFenceFailed):
                    return self._map_owner_failure(proof.code)
                if not isinstance(proof, OwnerActiveProof):
                    return ServiceFailed(SafeResultCode.OWNER_OPERATION_BLOCKED)
                if (
                    proof.identity_public_id != snapshot.owner_identity_public_id
                    or proof.provider_id != snapshot.provider_id
                ):
                    return ServiceFailed(SafeResultCode.OWNER_OPERATION_BLOCKED)
                relock = getattr(self._channel_port, "lock_unchanged", None)
                if callable(relock):
                    result = relock(ChannelRevisionProof.from_snapshot(snapshot))
                    if isinstance(result, ExactChannelSnapshotRejected):
                        return self._map_snapshot_failure(result.code)
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
        return None

    @staticmethod
    def _map_owner_failure(code: str) -> ServiceFailed:
        try:
            safe_code = SafeResultCode(code)
        except ValueError:
            safe_code = SafeResultCode.OWNER_OPERATION_BLOCKED
        return ServiceFailed(safe_code)

    @staticmethod
    def _map_snapshot_failure(code: str) -> ServiceFailed:
        mapping = {
            "channel_unavailable": SafeResultCode.CHANNEL_UNAVAILABLE,
            "channel_inactive": SafeResultCode.CHANNEL_INACTIVE,
            "stale_channel": SafeResultCode.STALE_CHANNEL,
            "credential_unavailable": SafeResultCode.CHANNEL_UNAVAILABLE,
            "credential_unreadable": SafeResultCode.CHANNEL_UNAVAILABLE,
            "storage_retryable": SafeResultCode.STORAGE_RETRYABLE,
            "storage_unavailable": SafeResultCode.STORAGE_UNAVAILABLE,
        }
        return ServiceFailed(mapping.get(code, SafeResultCode.CHANNEL_UNAVAILABLE))

    @staticmethod
    def _map_gateway_mutation_failure(result) -> ServiceFailed | None:
        if isinstance(result, GatewayAccepted):
            return None
        if isinstance(result, GatewayRejected):
            mapping = {
                "line_rejected": SafeResultCode.LINE_REJECTED,
                "invalid_input": SafeResultCode.INVALID_INPUT,
                "image_invalid": SafeResultCode.IMAGE_INVALID,
            }
            return ServiceFailed(mapping.get(result.code, SafeResultCode.LINE_REJECTED))
        if isinstance(result, GatewayUnknown):
            mapping = {
                "timeout_unknown": SafeResultCode.TIMEOUT_UNKNOWN,
                "response_unknown": SafeResultCode.RESPONSE_UNKNOWN,
                "rate_limited": SafeResultCode.RATE_LIMITED,
            }
            return ServiceFailed(
                mapping.get(result.code, SafeResultCode.RESPONSE_UNKNOWN),
                next_allowed_actions=(NextAllowedAction.GET_STATE,),
            )
        return ServiceFailed(SafeResultCode.RESPONSE_UNKNOWN)
