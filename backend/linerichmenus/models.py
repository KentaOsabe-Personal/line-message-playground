import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class RichMenuChannelState(models.Model):
    class ObservationKind(models.TextChoices):
        DEFAULT_NONE = "default_none", "Default none"
        MANAGED_DEFAULT = "managed_default", "Managed default"
        OTHER_MANAGED_DEFAULT = "other_managed_default", "Other managed default"
        EXTERNAL_DEFAULT = "external_default", "External default"
        UNKNOWN = "unknown", "Unknown"

    channel_public_id = models.UUIDField(primary_key=True, serialize=False)
    blocking_operation = models.ForeignKey(
        "RichMenuOperation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="blocking_channel_states",
    )
    active_operation = models.ForeignKey(
        "RichMenuOperation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="active_channel_states",
    )
    current_resource = models.ForeignKey(
        "ManagedRichMenu",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_for_channel_states",
    )
    last_observation_kind = models.CharField(
        max_length=32, choices=ObservationKind.choices, null=True, blank=True
    )
    last_observation_fingerprint = models.CharField(
        max_length=64, null=True, blank=True
    )
    last_observed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "linerichmenus_channelstate"
        indexes = [
            models.Index(fields=("blocking_operation",), name="lrm_state_block_idx"),
            models.Index(fields=("active_operation",), name="lrm_state_active_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(last_observation_kind__isnull=True)
                    | Q(
                        last_observation_kind__in=(
                            "default_none",
                            "managed_default",
                            "other_managed_default",
                            "external_default",
                            "unknown",
                        )
                    )
                ),
                name="lrm_state_observation_kind_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        last_observation_kind__isnull=True,
                        last_observation_fingerprint__isnull=True,
                        last_observed_at__isnull=True,
                    )
                    | Q(
                        last_observation_kind__isnull=False,
                        last_observation_fingerprint__isnull=False,
                        last_observed_at__isnull=False,
                    )
                ),
                name="lrm_state_observation_complete",
            ),
        ]


class RichMenuOperation(models.Model):
    class Kind(models.TextChoices):
        APPLY = "apply", "Apply"
        UNLINK = "unlink", "Unlink"
        RELEASE = "release", "Release"
        RECHECK = "recheck", "Recheck"
        CLEANUP = "cleanup", "Cleanup"

    class Status(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        PROCESSING = "processing", "Processing"
        FAILED = "failed", "Failed"
        UNKNOWN = "unknown", "Unknown"
        CLEANUP_REQUIRED = "cleanup_required", "Cleanup required"
        RECOVERY_ACTIVE = "recovery_active", "Recovery active"
        SUCCEEDED = "succeeded", "Succeeded"

    class Stage(models.TextChoices):
        CREATING = "creating", "Creating"
        UPLOADING = "uploading", "Uploading"
        SETTING_DEFAULT = "setting_default", "Setting default"
        VERIFYING = "verifying", "Verifying"
        CLEARING_DEFAULT = "clearing_default", "Clearing default"
        CLEANING = "cleaning", "Cleaning"
        LOCAL_RELEASE = "local_release", "Local release"

    operation_id = models.UUIDField(primary_key=True, serialize=False)
    channel_state = models.ForeignKey(
        RichMenuChannelState,
        on_delete=models.CASCADE,
        related_name="operations",
    )
    owner_identity_public_id = models.UUIDField()
    provider_id = models.CharField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    subject_operation = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recovery_operations",
    )
    target_resource = models.ForeignKey(
        "ManagedRichMenu",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="targeted_operations",
    )
    request_fingerprint = models.CharField(max_length=64)
    confirmation_usage_digest = models.CharField(
        max_length=64, null=True, blank=True, unique=True
    )
    expected_channel_revision = models.DateTimeField()
    status = models.CharField(max_length=32, choices=Status.choices)
    stage = models.CharField(
        max_length=32, choices=Stage.choices, null=True, blank=True
    )
    stage_started_at = models.DateTimeField(null=True, blank=True)
    result_code = models.CharField(max_length=64)
    configuration_snapshot = models.JSONField(null=True, blank=True)
    accepted_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "linerichmenus_operation"
        indexes = [
            models.Index(
                fields=("channel_state", "accepted_at"), name="lrm_op_channel_time_idx"
            ),
            models.Index(fields=("subject_operation",), name="lrm_op_subject_idx"),
            models.Index(fields=("target_resource",), name="lrm_op_target_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(kind="apply", subject_operation__isnull=True, target_resource__isnull=True)
                    | Q(
                        kind__in=("unlink", "release"),
                        subject_operation__isnull=True,
                        target_resource__isnull=False,
                    )
                    | Q(
                        kind="recheck",
                        subject_operation__isnull=False,
                        target_resource__isnull=True,
                    )
                    | Q(
                        kind="cleanup",
                        subject_operation__isnull=False,
                        target_resource__isnull=False,
                    )
                ),
                name="lrm_operation_relation_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="accepted", stage__isnull=True)
                    | Q(status__in=("processing", "failed", "unknown", "cleanup_required", "recovery_active", "succeeded"), stage__isnull=False)
                ),
                name="lrm_operation_stage_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(stage__isnull=True)
                    | Q(
                        stage__in=(
                            "creating",
                            "uploading",
                            "setting_default",
                            "verifying",
                            "clearing_default",
                            "cleaning",
                            "local_release",
                        )
                    )
                ),
                name="lrm_operation_stage_choice",
            ),
        ]


class ManagedRichMenu(models.Model):
    class Lifecycle(models.TextChoices):
        CANDIDATE = "candidate", "Candidate"
        APPLIED = "applied", "Applied"
        OLD = "old", "Old"
        CLEANUP_REQUIRED = "cleanup_required", "Cleanup required"
        DELETED = "deleted", "Deleted"
        RELEASED = "released", "Released"

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)
    channel_state = models.ForeignKey(
        RichMenuChannelState,
        on_delete=models.CASCADE,
        related_name="managed_resources",
    )
    origin_operation = models.ForeignKey(
        RichMenuOperation,
        on_delete=models.PROTECT,
        related_name="originated_resources",
    )
    replacement_operation = models.OneToOneField(
        RichMenuOperation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replaced_resource",
    )
    line_rich_menu_id = models.CharField(
        max_length=128, null=True, blank=True, unique=True
    )
    ownership_marker = models.CharField(max_length=128, unique=True)
    lifecycle = models.CharField(max_length=32, choices=Lifecycle.choices)
    image_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "linerichmenus_resource"
        indexes = [
            models.Index(
                fields=("channel_state", "lifecycle"), name="lrm_res_channel_life_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    lifecycle__in=(
                        "candidate",
                        "applied",
                        "old",
                        "cleanup_required",
                        "deleted",
                        "released",
                    )
                ),
                name="lrm_resource_lifecycle_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        lifecycle__in=("candidate", "applied", "released"),
                        replacement_operation__isnull=True,
                    )
                    | Q(lifecycle__in=("old", "cleanup_required", "deleted"))
                ),
                name="lrm_resource_replacement_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(lifecycle="deleted", deleted_at__isnull=False, released_at__isnull=True)
                    | Q(lifecycle="released", released_at__isnull=False, deleted_at__isnull=True)
                    | Q(
                        lifecycle__in=("candidate", "applied", "old", "cleanup_required"),
                        deleted_at__isnull=True,
                        released_at__isnull=True,
                    )
                ),
                name="lrm_resource_terminal_time_valid",
            ),
        ]


class RichMenuOperationTransition(models.Model):
    operation = models.ForeignKey(
        RichMenuOperation,
        on_delete=models.CASCADE,
        related_name="transitions",
    )
    sequence = models.PositiveIntegerField()
    from_status = models.CharField(max_length=32, choices=RichMenuOperation.Status.choices)
    to_status = models.CharField(max_length=32, choices=RichMenuOperation.Status.choices)
    stage = models.CharField(max_length=32, choices=RichMenuOperation.Stage.choices)
    safe_reason = models.CharField(max_length=64)
    observed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "linerichmenus_transition"
        indexes = [
            models.Index(fields=("operation", "created_at"), name="lrm_trans_op_time_idx")
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("operation", "sequence"), name="lrm_transition_sequence_uniq"
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=1), name="lrm_transition_sequence_positive"
            ),
            models.CheckConstraint(
                condition=Q(from_status__in=RichMenuOperation.Status.values),
                name="lrm_transition_from_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(to_status__in=RichMenuOperation.Status.values),
                name="lrm_transition_to_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(stage__in=RichMenuOperation.Stage.values),
                name="lrm_transition_stage_valid",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("rich menu transitions are append-only")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("rich menu transitions are append-only")
