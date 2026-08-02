from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from .gateway import (
    GatewayUnknown,
    ImageObserved,
    ImageObservationUnknown,
    ResourceAbsent,
    ResourceListAccepted,
    ResourceObservationUnknown,
    ResourceObserved,
    RichMenuDefaultExternal,
    RichMenuDefaultNone,
    RichMenuDefaultPresent,
    RichMenuDefaultUnknown,
    RichMenuGateway,
    RichMenuGatewayContext,
)
from .types import (
    DefaultObservation,
    NextAllowedAction,
    ObservationKind,
    OperationStage,
    ResourceLifecycle,
)


@dataclass(frozen=True, slots=True, repr=False)
class ManagedResourceTarget:
    """LINE IDとの強い管理対象関係をrecheckへ明示的に渡す。"""

    public_id: UUID
    line_rich_menu_id: str | None
    lifecycle: ResourceLifecycle
    ownership_marker: str | None
    origin_operation_id: UUID
    replacement_operation_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.public_id, UUID):
            raise ValueError("invalid managed resource id")
        if self.line_rich_menu_id is not None and (
            not isinstance(self.line_rich_menu_id, str) or not self.line_rich_menu_id
        ):
            raise ValueError("invalid line rich menu id")
        if not isinstance(self.lifecycle, ResourceLifecycle):
            raise ValueError("invalid resource lifecycle")
        if self.ownership_marker is not None and not isinstance(self.ownership_marker, str):
            raise ValueError("invalid ownership marker")
        if not isinstance(self.origin_operation_id, UUID):
            raise ValueError("invalid origin operation id")
        if self.replacement_operation_id is not None and not isinstance(
            self.replacement_operation_id, UUID
        ):
            raise ValueError("invalid replacement operation id")

    @property
    def is_reclassifiable(self) -> bool:
        return self.lifecycle not in {
            ResourceLifecycle.DELETED,
            ResourceLifecycle.RELEASED,
        }

    def __repr__(self) -> str:
        return (
            f"<ManagedResourceTarget public_id={self.public_id} "
            f"lifecycle={self.lifecycle.value} line_rich_menu_id=redacted "
            "ownership_marker=redacted>"
        )


@dataclass(frozen=True, slots=True)
class ReconcileContext:
    gateway_context: RichMenuGatewayContext
    current_resource: ManagedResourceTarget | None = None
    managed_resources: tuple[ManagedResourceTarget, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.gateway_context, RichMenuGatewayContext):
            raise ValueError("invalid reconcile gateway context")
        if self.current_resource is not None and not isinstance(
            self.current_resource, ManagedResourceTarget
        ):
            raise ValueError("invalid current managed resource")
        if not isinstance(self.managed_resources, tuple) or not all(
            isinstance(resource, ManagedResourceTarget)
            for resource in self.managed_resources
        ):
            raise ValueError("invalid managed resource collection")


@dataclass(frozen=True, slots=True, repr=False)
class Reconciliation:
    observation: DefaultObservation
    next_allowed_actions: tuple[NextAllowedAction, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.observation, DefaultObservation):
            raise ValueError("invalid reconciliation observation")
        if not isinstance(self.next_allowed_actions, tuple) or not all(
            isinstance(action, NextAllowedAction)
            for action in self.next_allowed_actions
        ):
            raise ValueError("invalid reconciliation actions")

    def __repr__(self) -> str:
        return (
            f"<Reconciliation kind={self.observation.kind.value} "
            f"fingerprint={self.observation.fingerprint} "
            f"next_allowed_actions={self.next_allowed_actions!r}>"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RecheckContext:
    gateway_context: RichMenuGatewayContext
    stage: OperationStage
    subject_operation_id: UUID
    ownership_marker: str | None = None
    candidate: ManagedResourceTarget | None = None
    target: ManagedResourceTarget | None = None
    expected_image_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.gateway_context, RichMenuGatewayContext):
            raise ValueError("invalid recheck gateway context")
        if not isinstance(self.stage, OperationStage):
            raise ValueError("invalid recheck stage")
        if not isinstance(self.subject_operation_id, UUID):
            raise ValueError("invalid subject operation id")
        if self.ownership_marker is not None and not isinstance(
            self.ownership_marker, str
        ):
            raise ValueError("invalid ownership marker")
        for name, resource in (("candidate", self.candidate), ("target", self.target)):
            if resource is not None and not isinstance(resource, ManagedResourceTarget):
                raise ValueError(f"invalid {name} resource")
        if self.expected_image_digest is not None and not _is_sha256(
            self.expected_image_digest
        ):
            raise ValueError("invalid expected image digest")


@dataclass(frozen=True, slots=True, repr=False)
class RecheckConfirmed:
    stage: OperationStage
    line_rich_menu_id: str | None = None
    resource_id: UUID | None = None
    next_stage: OperationStage | None = None
    status: str = "confirmed"

    def __post_init__(self) -> None:
        if not isinstance(self.stage, OperationStage):
            raise ValueError("invalid confirmed stage")
        if self.line_rich_menu_id is not None and not isinstance(
            self.line_rich_menu_id, str
        ):
            raise ValueError("invalid confirmed line id")
        if self.resource_id is not None and not isinstance(self.resource_id, UUID):
            raise ValueError("invalid confirmed resource id")
        if self.next_stage is not None and not isinstance(self.next_stage, OperationStage):
            raise ValueError("invalid confirmed next stage")
        if self.status != "confirmed":
            raise ValueError("invalid confirmed status")

    def __repr__(self) -> str:
        return (
            f"<RecheckConfirmed stage={self.stage.value} "
            "line_rich_menu_id=redacted "
            f"resource_id={self.resource_id} next_stage={self.next_stage}>"
        )


@dataclass(frozen=True, slots=True)
class RecheckUnknown:
    stage: OperationStage
    reason: str
    status: str = "unknown"
    next_allowed_actions: tuple[NextAllowedAction, ...] = (
        NextAllowedAction.RECHECK,
        NextAllowedAction.GET_STATE,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.stage, OperationStage):
            raise ValueError("invalid unresolved stage")
        if self.reason not in {
            "observation_unknown",
            "ambiguous_resource",
            "not_confirmed",
            "ownership_unverified",
            "invalid_context",
        }:
            raise ValueError("invalid unresolved reason")
        if self.status != "unknown":
            raise ValueError("invalid unresolved status")
        if not isinstance(self.next_allowed_actions, tuple) or not all(
            isinstance(action, NextAllowedAction)
            for action in self.next_allowed_actions
        ):
            raise ValueError("invalid unresolved next actions")


RecheckResult = RecheckConfirmed | RecheckUnknown


class RichMenuReconciler(Protocol):
    def observe_channel(self, context: ReconcileContext) -> Reconciliation: ...

    def recheck_operation(self, context: RecheckContext) -> RecheckResult: ...


class DefaultRichMenuReconciler:
    def __init__(self, gateway: RichMenuGateway) -> None:
        self._gateway = gateway

    def observe_channel(self, context: ReconcileContext) -> Reconciliation:
        if not isinstance(context, ReconcileContext):
            raise TypeError("reconcile context required")
        observed = self._gateway.get_default(context.gateway_context)
        now = context.gateway_context.channel_revision
        if isinstance(observed, RichMenuDefaultNone):
            return self._result(
                ObservationKind.DEFAULT_NONE,
                now,
                managed_resource_id=None,
                actions=(
                    NextAllowedAction.APPLY,
                    NextAllowedAction.GET_STATE,
                    NextAllowedAction.VIEW_HISTORY,
                ),
            )
        if isinstance(observed, RichMenuDefaultExternal):
            return self._result(
                ObservationKind.EXTERNAL_DEFAULT,
                now,
                managed_resource_id=None,
                actions=(
                    NextAllowedAction.APPLY,
                    NextAllowedAction.GET_STATE,
                    NextAllowedAction.VIEW_HISTORY,
                ),
            )
        if isinstance(observed, RichMenuDefaultUnknown):
            return self._result(
                ObservationKind.UNKNOWN,
                now,
                managed_resource_id=None,
                actions=(NextAllowedAction.RECHECK, NextAllowedAction.GET_STATE),
            )
        if isinstance(observed, RichMenuDefaultPresent):
            managed_resources = context.managed_resources
            if context.current_resource is not None and all(
                resource.public_id != context.current_resource.public_id
                for resource in managed_resources
            ):
                managed_resources = (context.current_resource, *managed_resources)
            matching = next(
                (
                    resource
                    for resource in managed_resources
                    if resource.line_rich_menu_id == observed.line_rich_menu_id
                    and resource.is_reclassifiable
                ),
                None,
            )
            if matching is None:
                kind = ObservationKind.EXTERNAL_DEFAULT
                resource_id = None
            elif (
                context.current_resource is not None
                and matching.public_id == context.current_resource.public_id
            ):
                kind = ObservationKind.MANAGED_DEFAULT
                resource_id = matching.public_id
            else:
                kind = ObservationKind.OTHER_MANAGED_DEFAULT
                resource_id = matching.public_id
            actions = (
                (NextAllowedAction.UNLINK, NextAllowedAction.RELEASE)
                if kind is ObservationKind.MANAGED_DEFAULT
                else (NextAllowedAction.APPLY,)
            )
            return self._result(
                kind,
                now,
                managed_resource_id=resource_id,
                actions=actions
                + (NextAllowedAction.GET_STATE, NextAllowedAction.VIEW_HISTORY),
            )
        return self._result(
            ObservationKind.UNKNOWN,
            now,
            managed_resource_id=None,
            actions=(NextAllowedAction.RECHECK, NextAllowedAction.GET_STATE),
        )

    def recheck_operation(self, context: RecheckContext) -> RecheckResult:
        if not isinstance(context, RecheckContext):
            raise TypeError("recheck context required")
        if context.stage is OperationStage.CREATING:
            return self._recheck_create(context)
        if context.stage is OperationStage.UPLOADING:
            return self._recheck_upload(context)
        if context.stage is OperationStage.SETTING_DEFAULT:
            return self._recheck_set_default(context)
        if context.stage is OperationStage.CLEARING_DEFAULT:
            return self._recheck_clear_default(context)
        if context.stage is OperationStage.CLEANING:
            return self._recheck_cleanup(context)
        if context.stage is OperationStage.VERIFYING:
            return self._recheck_set_default(context)
        return RecheckUnknown(context.stage, "invalid_context")

    def _recheck_create(self, context: RecheckContext) -> RecheckResult:
        if not context.ownership_marker:
            return RecheckUnknown(context.stage, "ownership_unverified")
        resources = self._gateway.list_resources(context.gateway_context)
        if isinstance(resources, GatewayUnknown):
            return RecheckUnknown(context.stage, "observation_unknown")
        if not isinstance(resources, ResourceListAccepted):
            return RecheckUnknown(context.stage, "observation_unknown")
        matches = tuple(
            resource
            for resource in resources.resources
            if resource.name == context.ownership_marker
        )
        if len(matches) != 1:
            return RecheckUnknown(context.stage, "ambiguous_resource")
        return RecheckConfirmed(
            context.stage,
            line_rich_menu_id=matches[0].line_rich_menu_id,
            next_stage=OperationStage.UPLOADING,
        )

    def _recheck_upload(self, context: RecheckContext) -> RecheckResult:
        candidate = context.candidate
        if (
            candidate is None
            or not candidate.line_rich_menu_id
            or candidate.origin_operation_id is None
            or not candidate.ownership_marker
            or context.expected_image_digest is None
        ):
            return RecheckUnknown(context.stage, "ownership_unverified")
        image = self._gateway.download(
            context.gateway_context, candidate.line_rich_menu_id
        )
        if isinstance(image, ImageObservationUnknown):
            return RecheckUnknown(context.stage, "observation_unknown")
        if not isinstance(image, ImageObserved):
            return RecheckUnknown(context.stage, "observation_unknown")
        if image.pixel_digest != context.expected_image_digest:
            return RecheckUnknown(context.stage, "not_confirmed")
        return RecheckConfirmed(
            context.stage,
            line_rich_menu_id=candidate.line_rich_menu_id,
            resource_id=candidate.public_id,
            next_stage=OperationStage.SETTING_DEFAULT,
        )

    def _recheck_set_default(self, context: RecheckContext) -> RecheckResult:
        candidate = context.candidate
        if candidate is None or not candidate.line_rich_menu_id:
            return RecheckUnknown(context.stage, "ownership_unverified")
        observed = self._gateway.get_default(context.gateway_context)
        if isinstance(observed, RichMenuDefaultPresent) and (
            observed.line_rich_menu_id == candidate.line_rich_menu_id
        ):
            return RecheckConfirmed(
                context.stage,
                line_rich_menu_id=candidate.line_rich_menu_id,
                resource_id=candidate.public_id,
                next_stage=OperationStage.VERIFYING,
            )
        if isinstance(observed, (RichMenuDefaultUnknown,)):
            return RecheckUnknown(context.stage, "observation_unknown")
        return RecheckUnknown(context.stage, "not_confirmed")

    def _recheck_clear_default(self, context: RecheckContext) -> RecheckResult:
        candidate = context.candidate
        if candidate is None or not candidate.line_rich_menu_id:
            return RecheckUnknown(context.stage, "ownership_unverified")
        observed = self._gateway.get_default(context.gateway_context)
        if isinstance(observed, RichMenuDefaultUnknown):
            return RecheckUnknown(context.stage, "observation_unknown")
        if isinstance(observed, RichMenuDefaultPresent) and (
            observed.line_rich_menu_id == candidate.line_rich_menu_id
        ):
            return RecheckUnknown(context.stage, "not_confirmed")
        return RecheckConfirmed(
            context.stage,
            line_rich_menu_id=candidate.line_rich_menu_id,
            resource_id=candidate.public_id,
            next_stage=OperationStage.CLEANING,
        )

    def _recheck_cleanup(self, context: RecheckContext) -> RecheckResult:
        target = context.target
        if (
            target is None
            or not target.line_rich_menu_id
            or not target.ownership_marker
            or not _resource_matches_subject(target, context.subject_operation_id)
            or target.lifecycle
            not in {
                ResourceLifecycle.CANDIDATE,
                ResourceLifecycle.OLD,
                ResourceLifecycle.CLEANUP_REQUIRED,
            }
        ):
            return RecheckUnknown(context.stage, "ownership_unverified")

        resource = self._gateway.get_resource(
            context.gateway_context, target.line_rich_menu_id
        )
        if isinstance(resource, ResourceObservationUnknown):
            return RecheckUnknown(context.stage, "observation_unknown")
        if isinstance(resource, ResourceObserved):
            return RecheckUnknown(context.stage, "not_confirmed")
        if not isinstance(resource, ResourceAbsent):
            return RecheckUnknown(context.stage, "observation_unknown")

        resources = self._gateway.list_resources(context.gateway_context)
        if isinstance(resources, GatewayUnknown) or not isinstance(
            resources, ResourceListAccepted
        ):
            return RecheckUnknown(context.stage, "observation_unknown")
        if any(
            item.line_rich_menu_id == target.line_rich_menu_id
            or item.name == target.ownership_marker
            for item in resources.resources
        ):
            return RecheckUnknown(context.stage, "not_confirmed")

        observed = self._gateway.get_default(context.gateway_context)
        if isinstance(observed, RichMenuDefaultUnknown):
            return RecheckUnknown(context.stage, "observation_unknown")
        if isinstance(observed, RichMenuDefaultPresent) and (
            observed.line_rich_menu_id == target.line_rich_menu_id
        ):
            return RecheckUnknown(context.stage, "not_confirmed")
        return RecheckConfirmed(
            context.stage,
            line_rich_menu_id=target.line_rich_menu_id,
            resource_id=target.public_id,
        )

    def _result(
        self,
        kind: ObservationKind,
        observed_at,
        *,
        managed_resource_id: UUID | None,
        actions: tuple[NextAllowedAction, ...],
    ) -> Reconciliation:
        return Reconciliation(
            observation=DefaultObservation(
                kind=kind,
                observed_at=observed_at,
                fingerprint=_observation_fingerprint(kind, managed_resource_id),
                managed_resource_id=managed_resource_id,
            ),
            next_allowed_actions=actions,
        )


def _observation_fingerprint(kind: ObservationKind, resource_id: UUID | None) -> str:
    value = f"{kind.value}:{resource_id or ''}".encode("utf-8")
    return sha256(value).hexdigest()


def _resource_matches_subject(
    resource: ManagedResourceTarget, subject_operation_id: UUID
) -> bool:
    return resource.origin_operation_id == subject_operation_id or (
        resource.replacement_operation_id == subject_operation_id
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
