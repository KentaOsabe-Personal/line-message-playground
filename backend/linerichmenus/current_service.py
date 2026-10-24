"""現在のリッチメニューを一度だけ観測する閲覧ユースケース。"""

from dataclasses import replace
from datetime import datetime
from typing import Callable, Protocol
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
    ChannelSnapshotCommand,
    ConnectionRevisionUnchanged,
    ExactChannelSnapshotAvailable,
    RichMenuChannelSnapshot,
)

from .current_types import (
    CurrentContent,
    CurrentImageAvailable,
    CurrentImageUnavailable,
    CurrentMenuSucceeded,
    CurrentReadMatched,
    CurrentReadRepository,
)
from .gateway import (
    DownloadedImage,
    ImageAbsent,
    ImageObservationUnknown,
    RichMenuGateway,
    RichMenuGatewayContext,
)
from .reconciliation import (
    DefaultRichMenuReconciler,
    ManagedResourceTarget,
    ReconcileContext,
    Reconciliation,
)
from .services import (
    DefaultMutationReadiness,
    DefaultRichMenuService,
    MutationReadiness,
    ServiceFailed,
)
from .types import (
    ChannelStateView,
    DefaultObservation,
    NextAllowedAction,
    ObservationKind,
    OperationStatus,
    OwnerChannelScope,
    ResourceLifecycle,
    SafeResultCode,
)


class CurrentChannelPort(Protocol):
    def get_for_owner_provider(self, public_id: UUID, owner_provider_id: str): ...
    def lock_connection_revision(
        self, public_id: UUID, owner_provider_id: str, expected_updated_at: datetime
    ): ...
    def snapshot_exact(self, command: ChannelSnapshotCommand): ...


class CurrentStateRepository(Protocol):
    def get_state(self, scope: OwnerChannelScope) -> ChannelStateView | None: ...
    def record_observation(
        self, scope: OwnerChannelScope, observation: DefaultObservation
    ) -> bool: ...


class CurrentMenuService:
    def __init__(
        self,
        *,
        owner_fence: OwnerOperationFence,
        channel_port: CurrentChannelPort,
        current_repository: CurrentReadRepository,
        repository: CurrentStateRepository,
        gateway: RichMenuGateway,
        reconciler=None,
        readiness: MutationReadiness | None = None,
        using: str = "default",
        clock: Callable[[], datetime] = timezone.now,
    ):
        self._owner_fence = owner_fence
        self._channel_port = channel_port
        self._current_repository = current_repository
        self._repository = repository
        self._gateway = gateway
        self._reconciler = reconciler or DefaultRichMenuReconciler(gateway)
        self._readiness = readiness or DefaultMutationReadiness(
            mode="read_only", integration_complete=False
        )
        self._using = using
        self._clock = clock

    def get_current(
        self, owner: OwnerOperationContext, channel_id: UUID, expected_channel_revision: datetime
    ) -> CurrentMenuSucceeded | ServiceFailed:
        if not isinstance(owner, OwnerOperationContext):
            return ServiceFailed(SafeResultCode.AUTHENTICATION_REQUIRED)
        if (
            not isinstance(channel_id, UUID)
            or not isinstance(expected_channel_revision, datetime)
            or timezone.is_naive(expected_channel_revision)
        ):
            return ServiceFailed(SafeResultCode.INVALID_INPUT)
        try:
            with transaction.atomic(using=self._using):
                authorized = self._authorize(owner, channel_id, expected_channel_revision)
                if isinstance(authorized, ServiceFailed):
                    return authorized
                proof, metadata = authorized
                scope = OwnerChannelScope(proof.identity_public_id, proof.provider_id, channel_id)
                credentials = None
                if metadata.is_active:
                    resolved = self._channel_port.snapshot_exact(
                        ChannelSnapshotCommand(
                            channel_id,
                            proof.identity_public_id,
                            proof.provider_id,
                            expected_channel_revision,
                        )
                    )
                    if not isinstance(resolved, ExactChannelSnapshotAvailable):
                        return DefaultRichMenuService._map_snapshot_failure(
                            getattr(resolved, "code", "channel_unavailable")
                        )
                    credentials = resolved.snapshot
                    if (
                        not isinstance(credentials, RichMenuChannelSnapshot)
                        or credentials.owner_identity_public_id != proof.identity_public_id
                        or credentials.provider_id != proof.provider_id
                        or credentials.channel_public_id != channel_id
                        or credentials.channel_revision != expected_channel_revision
                        or not credentials.is_active
                    ):
                        return ServiceFailed(SafeResultCode.STALE_CHANNEL)
                captured = self._current_repository.capture_current(scope)
                stored = self._repository.get_state(scope) or DefaultRichMenuService._empty_state(
                    channel_id
                )
                if not metadata.is_active:
                    return self._result(
                        scope,
                        expected_channel_revision,
                        captured,
                        stored,
                        CurrentContent("inactive"),
                        None,
                    )
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)

        context = RichMenuGatewayContext(
            channel_id, expected_channel_revision, credentials.access_token
        )
        resources = tuple(
            ManagedResourceTarget(
                resource.public_id,
                resource.line_rich_menu_id,
                resource.lifecycle,
                None,
                resource.origin_operation_id,
                resource.replacement_operation_id,
            )
            for resource in captured.resources
            if resource.channel_public_id == channel_id
            and any(
                operation.operation_id == resource.origin_operation_id
                and operation.owner_identity_public_id == scope.owner_identity_public_id
                and operation.provider_id == scope.provider_id
                and operation.channel_public_id == channel_id
                for operation in captured.operations
            )
        )
        current = next(
            (
                resource
                for resource in resources
                if captured.state is not None
                and resource.public_id == captured.state.current_resource_id
            ),
            None,
        )
        reconcile_context = ReconcileContext(context, current, resources)
        observed = self._observe(reconcile_context)
        if isinstance(observed, ServiceFailed):
            return observed
        observation = observed.observation
        # Reconcilerのrevision由来日時を、実際の観測時刻へ置き換える。
        observation = replace(observation, observed_at=self._clock())
        busy = captured.state is not None and captured.state.active_operation_id is not None
        blocker = next(
            (
                operation
                for operation in captured.operations
                if captured.state is not None
                and operation.operation_id == captured.state.blocking_operation_id
            ),
            None,
        )
        uncertain = (
            captured.state is not None
            and captured.state.blocking_operation_id is not None
            and (blocker is None or blocker.status.value != "cleanup_required")
        )
        if (
            observation.kind is ObservationKind.MANAGED_DEFAULT
            and current is not None
            # 設定の編集可否ではなく、作成元applyと確定状態を含む所有関係。
            and captured.release_target_id == current.public_id
            and not busy
            and not uncertain
        ):
            try:
                image = self._gateway.download_content(context, current.line_rich_menu_id)
            except Exception:
                image = ImageObservationUnknown()
            second = self._observe(reconcile_context)
            if isinstance(second, ServiceFailed):
                return second
            if (
                second.observation.fingerprint != observation.fingerprint
                or second.observation.kind is not ObservationKind.MANAGED_DEFAULT
                or second.observation.managed_resource_id != current.public_id
            ):
                return ServiceFailed(SafeResultCode.CURRENT_CHANGED)
            observation = replace(second.observation, observed_at=self._clock())
            if isinstance(image, DownloadedImage):
                resource = next(
                    resource
                    for resource in captured.resources
                    if resource.public_id == current.public_id
                )
                if image.pixel_digest != resource.image_digest:
                    return ServiceFailed(SafeResultCode.CURRENT_CHANGED)
                image_view = CurrentImageAvailable(
                    image.content_type, image.width, image.height, image.pixel_digest, image.binary
                )
            else:
                reason = (
                    "missing"
                    if isinstance(image, ImageAbsent)
                    else "rate_limited"
                    if isinstance(image, ImageObservationUnknown) and image.code == "rate_limited"
                    else "invalid"
                    if getattr(image, "code", None) == "image_invalid"
                    else "fetch_failed"
                )
                image_view = CurrentImageUnavailable(reason)
            content = CurrentContent(
                "managed", current.public_id, image_view, captured.configuration
            )
        elif (
            captured.state is not None
            and captured.state.current_resource_id is not None
            or busy
            or uncertain
        ):
            content = CurrentContent("attention")
        else:
            content = CurrentContent(
                "unset"
                if observation.kind is ObservationKind.DEFAULT_NONE
                else "external"
                if observation.kind is ObservationKind.EXTERNAL_DEFAULT
                else "attention"
            )
        try:
            with transaction.atomic(using=self._using):
                authorized = self._authorize(owner, channel_id, expected_channel_revision)
                if isinstance(authorized, ServiceFailed):
                    return authorized
                final_proof, metadata = authorized
                if final_proof != proof:
                    return ServiceFailed(SafeResultCode.OWNER_OPERATION_BLOCKED)
                if not metadata.is_active:
                    return ServiceFailed(SafeResultCode.STALE_CHANNEL)
                matched = self._current_repository.lock_current_unchanged(scope, captured)
                if not isinstance(matched, CurrentReadMatched):
                    return ServiceFailed(SafeResultCode.CURRENT_CHANGED)
                if self._repository.record_observation(scope, observation) is not True:
                    transaction.set_rollback(True, using=self._using)
                    return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)
                return self._result(
                    scope,
                    expected_channel_revision,
                    matched.snapshot,
                    replace(stored, latest_observation=observation),
                    content,
                    observation.observed_at,
                )
        except Exception:
            return ServiceFailed(SafeResultCode.STORAGE_UNAVAILABLE)

    def _authorize(self, owner, channel_id, revision):
        proof = self._owner_fence.lock_active(owner, self._clock())
        if isinstance(proof, OwnerFenceFailed):
            return DefaultRichMenuService._map_owner_failure(proof.code)
        if (
            not isinstance(proof, OwnerActiveProof)
            or proof.identity_public_id != owner.identity_public_id
        ):
            return ServiceFailed(SafeResultCode.OWNER_OPERATION_BLOCKED)
        locked = self._channel_port.lock_connection_revision(
            channel_id, proof.provider_id, revision
        )
        if not isinstance(locked, ConnectionRevisionUnchanged):
            return DefaultRichMenuService._map_snapshot_failure(
                getattr(locked, "code", "channel_unavailable")
            )
        metadata = self._channel_port.get_for_owner_provider(channel_id, proof.provider_id)
        if (
            metadata is None
            or metadata.provider_id != proof.provider_id
            or metadata.public_id != channel_id
        ):
            return ServiceFailed(SafeResultCode.CHANNEL_UNAVAILABLE)
        if metadata.updated_at != revision:
            return ServiceFailed(SafeResultCode.STALE_CHANNEL)
        return proof, metadata

    def _observe(self, context):
        try:
            result = self._reconciler.observe_channel(context)
        except Exception:
            return ServiceFailed(SafeResultCode.OBSERVATION_UNKNOWN)
        if (
            not isinstance(result, Reconciliation)
            or result.observation.kind is ObservationKind.UNKNOWN
        ):
            return ServiceFailed(SafeResultCode.OBSERVATION_UNKNOWN)
        return result

    def _result(self, scope, revision, captured, state, content, checked_at):
        actions, recovery_targets = self._actions(scope, captured, state, content)
        rich = replace(
            state,
            next_allowed_actions=actions,
            capabilities=self._readiness.project(
                actions, channel_active=content.kind != "inactive"
            ),
        )
        return CurrentMenuSucceeded(
            scope.channel_public_id,
            revision,
            checked_at,
            rich,
            captured.deactivation,
            content,
            recovery_targets,
        )

    @staticmethod
    def _actions(scope, captured, state, content):
        read = (NextAllowedAction.GET_STATE, NextAllowedAction.VIEW_HISTORY)
        if content.kind == "inactive":
            return read, ()
        pending = captured.deactivation is not None and captured.deactivation.status != "completed"
        refs = captured.state
        if refs is not None and refs.active_operation_id is not None:
            return read, ()
        blocker = next(
            (
                operation
                for operation in captured.operations
                if refs is not None
                and operation.operation_id == refs.blocking_operation_id
                and operation.channel_public_id == scope.channel_public_id
                and operation.owner_identity_public_id == scope.owner_identity_public_id
                and operation.provider_id == scope.provider_id
            ),
            None,
        )
        if refs is not None and refs.blocking_operation_id is not None:
            if blocker is None:
                return read, ()
            if blocker.status is OperationStatus.UNKNOWN and not pending:
                return (NextAllowedAction.RECHECK, *read), ()
            targets = captured.recovery_targets
            cleanup_allowed = not pending or captured.deactivation.safe_reason == "cleanup_required"
            if blocker.status is OperationStatus.CLEANUP_REQUIRED and targets and cleanup_allowed:
                return (NextAllowedAction.CLEANUP, *read), targets
            return read, ()
        if (
            pending
            or state.cleanup_resources
            or any(
                resource.lifecycle in {ResourceLifecycle.OLD, ResourceLifecycle.CLEANUP_REQUIRED}
                for resource in captured.resources
            )
        ):
            return read, ()
        observation = state.latest_observation
        if observation is None:
            return read, ()
        current_id = None if refs is None else refs.current_resource_id
        valid_current = current_id is not None and captured.release_target_id == current_id
        if (
            observation.kind is ObservationKind.MANAGED_DEFAULT
            and valid_current
            and observation.managed_resource_id == current_id
        ):
            return (
                NextAllowedAction.NEW_PREVIEW,
                NextAllowedAction.APPLY,
                NextAllowedAction.UNLINK,
                NextAllowedAction.RELEASE,
                *read,
            ), ()
        if observation.kind in {
            ObservationKind.DEFAULT_NONE,
            ObservationKind.EXTERNAL_DEFAULT,
            ObservationKind.OTHER_MANAGED_DEFAULT,
        }:
            if current_id is not None and not valid_current:
                return read, ()
            if current_id is None and observation.kind is ObservationKind.OTHER_MANAGED_DEFAULT:
                return read, ()
            release = (NextAllowedAction.RELEASE,) if valid_current else ()
            return (NextAllowedAction.NEW_PREVIEW, NextAllowedAction.APPLY, *release, *read), ()
        return read, ()
