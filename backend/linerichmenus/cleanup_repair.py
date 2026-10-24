"""旧版が成功確定してしまった置換を、明示的な管理操作で後片付けへ戻す。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from linechannels.admin_lifecycle_types import DeactivationConflict
from linechannels.admin_types import ConnectionRevisionUnchanged

from .models import ManagedRichMenu, RichMenuChannelState, RichMenuOperation
from .repository import DjangoRichMenuRepository
from .types import OperationStage, OperationStatus, SafeResultCode


class CleanupRepairRejected(Exception):
    """秘密情報を含まない修復拒否。"""


@dataclass(frozen=True, slots=True)
class CleanupRepairPlan:
    status: str
    subject_operation_id: UUID
    target_resource_id: UUID
    remaining_old_resources: int


class CleanupStateRepair:
    def __init__(self, *, accounts, channels, deactivation, using="default", clock=timezone.now):
        self.accounts = accounts
        self.channels = channels
        self.deactivation = deactivation
        self.using = using
        self.clock = clock

    def run(
        self,
        channel_id: UUID,
        revision: datetime,
        *,
        apply=False,
        subject_id: UUID | None = None,
        target_id: UUID | None = None,
    ) -> CleanupRepairPlan:
        if (
            not isinstance(channel_id, UUID)
            or not isinstance(revision, datetime)
            or timezone.is_naive(revision)
        ):
            raise CleanupRepairRejected("invalid_input")
        if apply and (not isinstance(subject_id, UUID) or not isinstance(target_id, UUID)):
            raise CleanupRepairRejected("confirmation_required")
        with transaction.atomic(using=self.using):
            owner = self.accounts.lock_owner_account()
            if owner.state != "active" or owner.identity_id is None:
                raise CleanupRepairRejected("owner_unavailable")
            identity = self.accounts.get_identity(owner.identity_id)
            if identity is None:
                raise CleanupRepairRejected("owner_unavailable")
            locked = self.channels.lock_connection_revision(
                channel_id, identity.provider_id, revision
            )
            if not isinstance(locked, ConnectionRevisionUnchanged):
                raise CleanupRepairRejected("stale_or_unavailable_channel")
            channel = self.channels.get_for_owner_provider(channel_id, identity.provider_id)
            if channel is None or not channel.is_active:
                raise CleanupRepairRejected("channel_inactive")
            intent = self.deactivation.get_for_owner(
                identity.public_id, identity.provider_id, channel_id
            )
            if isinstance(intent, DeactivationConflict) or (
                intent is not None and intent.status != "completed"
            ):
                raise CleanupRepairRejected("deactivation_pending")
            state = (
                RichMenuChannelState.objects.using(self.using)
                .select_for_update()
                .filter(pk=channel_id)
                .first()
            )
            if state is None or state.active_operation_id is not None:
                raise CleanupRepairRejected("operation_in_progress")
            resources = list(
                ManagedRichMenu.objects.using(self.using)
                .select_for_update()
                .filter(channel_state=state)
                .order_by("public_id")
            )
            references = {
                value
                for resource in resources
                for value in (resource.origin_operation_id, resource.replacement_operation_id)
                if value is not None
            }
            operations = {
                operation.pk: operation
                for operation in RichMenuOperation.objects.using(self.using)
                .select_for_update()
                .filter(pk__in=references)
                .order_by("operation_id")
            }

            def owned(operation):
                return (
                    operation is not None
                    and operation.channel_state_id == channel_id
                    and operation.owner_identity_public_id == identity.public_id
                    and operation.provider_id == identity.provider_id
                    and operation.kind == "apply"
                )

            current = next(
                (resource for resource in resources if resource.pk == state.current_resource_id),
                None,
            )
            current_origin = (
                None if current is None else operations.get(current.origin_operation_id)
            )
            if (
                current is None
                or current.lifecycle != "applied"
                or not owned(current_origin)
                or current_origin.status not in {"succeeded", "cleanup_required"}
            ):
                raise CleanupRepairRejected("current_mapping_unavailable")
            old = [resource for resource in resources if resource.lifecycle == "old"]
            candidates = []
            for resource in old:
                # 置換先を削除して前任の所有証明を失わないよう、因果順に古い資源から扱う。
                if any(
                    previous.replacement_operation_id == resource.origin_operation_id
                    for previous in old
                    if previous.pk != resource.pk
                ):
                    continue
                subject = operations.get(resource.replacement_operation_id)
                origin = operations.get(resource.origin_operation_id)
                successor = next(
                    (
                        value
                        for value in resources
                        if subject is not None
                        and value.origin_operation_id == subject.pk
                        and value.lifecycle in {"applied", "old"}
                    ),
                    None,
                )
                if not owned(subject) or not owned(origin) or successor is None:
                    continue
                if origin.status not in {"succeeded", "cleanup_required"}:
                    continue
                if (
                    subject.pk == origin.pk
                    or resource.pk == current.pk
                    or resource.line_rich_menu_id is None
                ):
                    continue
                if resource.line_rich_menu_id == current.line_rich_menu_id:
                    continue
                if resource.deleted_at is not None or resource.released_at is not None:
                    continue
                if subject_id is not None and subject.pk != subject_id:
                    continue
                if target_id is not None and resource.pk != target_id:
                    continue
                already = (
                    state.blocking_operation_id == subject.pk
                    and subject.status == "cleanup_required"
                    and subject.stage == "cleaning"
                )
                if not already and (
                    subject.status != "succeeded"
                    or subject.stage != "verifying"
                    or subject.result_code != "succeeded"
                ):
                    continue
                candidates.append((resource, subject, already))
            if not candidates:
                raise CleanupRepairRejected("no_proven_replacement")
            resource, subject, already = min(
                candidates, key=lambda value: (value[1].accepted_at, value[1].pk)
            )
            if state.blocking_operation_id is not None and not already:
                raise CleanupRepairRejected("operation_in_progress")
            if already:
                return CleanupRepairPlan("already_repaired", subject.pk, resource.pk, len(old))
            if not apply:
                return CleanupRepairPlan("dry_run", subject.pk, resource.pk, len(old))
            # 通常のterminal遷移は変更しない。修復は元の成功履歴の後に訂正を追記する。
            recorder = DjangoRichMenuRepository(using=self.using, clock=self.clock)
            recorder._append_transition(
                operation=subject,
                from_status=OperationStatus.SUCCEEDED,
                to_status=OperationStatus.CLEANUP_REQUIRED,
                stage=OperationStage.CLEANING,
                reason=SafeResultCode.CLEANUP_REQUIRED,
            )
            subject.status = "cleanup_required"
            subject.stage = "cleaning"
            subject.result_code = "cleanup_required"
            subject.stage_started_at = None
            subject.save(
                using=self.using,
                update_fields=("status", "stage", "result_code", "stage_started_at", "updated_at"),
            )
            state.blocking_operation = subject
            state.save(using=self.using, update_fields=("blocking_operation", "updated_at"))
            return CleanupRepairPlan("repaired", subject.pk, resource.pk, len(old))
