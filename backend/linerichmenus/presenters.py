from __future__ import annotations

from .services import (
    HistorySucceeded,
    OperationSucceeded,
    PreviewSucceeded,
    ServiceFailed,
    StateSucceeded,
)
from .types import (
    ChannelStateView,
    DefaultObservation,
    HistoryEntry,
    ManagedResourceView,
    OperationView,
    TemplateDescriptor,
)


_DOMAIN_TO_API_FIELDS = {
    "request": "request",
    "template": "templateId",
    "templateId": "templateId",
    "templateVersion": "templateVersion",
    "channelRevision": "channelRevision",
    "fields": "fields",
    "kind": "kind",
    "operationId": "operationId",
    "confirmationToken": "confirmationToken",
    "subjectOperationId": "subjectOperationId",
    "targetResourceId": "targetResourceId",
    "cursor": "cursor",
    "limit": "limit",
}
_SAFE_AREA_FIELDS = frozenset({"area1", "area2", "area3"})


def _api_field_path(value: str) -> str | None:
    if value in _DOMAIN_TO_API_FIELDS:
        return _DOMAIN_TO_API_FIELDS[value]
    if value in _SAFE_AREA_FIELDS:
        return value
    parts = value.split(".")
    if (
        len(parts) == 2
        and parts[0] in _SAFE_AREA_FIELDS
        and parts[1] in {"displayName", "uri"}
    ):
        return value
    return None


def _iso(value):
    return None if value is None else value.isoformat()


class RichMenuPresenter:
    def templates(self, templates: tuple[TemplateDescriptor, ...]) -> dict[str, object]:
        return {
            "items": [
                {
                    "templateId": item.reference.template_id,
                    "version": item.reference.version,
                    "displayName": item.display_name,
                    "canvas": {"width": item.width, "height": item.height},
                    "areas": [
                        {
                            "field": area.field_name,
                            "description": area.description,
                            "bounds": {
                                "x": area.x, "y": area.y,
                                "width": area.width, "height": area.height,
                            },
                        }
                        for area in item.areas
                    ],
                    "requiredFields": list(item.required_fields),
                    "limits": {
                        "displayName": item.display_name_limit,
                        "uri": item.uri_limit,
                    },
                }
                for item in templates
            ]
        }

    def preview(self, result: PreviewSucceeded) -> dict[str, object]:
        preview = result.preview
        return {
            "channelId": str(preview.channel_public_id),
            "channelLabel": preview.channel_label,
            "templateId": preview.template.reference.template_id,
            "templateVersion": preview.template.reference.version,
            "fields": [
                {"displayName": field.display_name, "uri": field.uri}
                for field in preview.template.fields
            ],
            "image": {
                "contentType": result.image.content_type,
                "width": result.image.width,
                "height": result.image.height,
                "digest": result.image.pixel_digest,
                "base64": result.image_base64,
            },
            "observation": self._observation(preview.observation),
            "warnings": [warning.value for warning in preview.warnings],
            "confirmationToken": result.token,
            "expiresAt": result.expires_at.isoformat(),
        }

    def state(self, result: StateSucceeded) -> dict[str, object]:
        state = result.state
        return {
            "channelId": str(state.channel_public_id),
            "currentResource": self._resource(state.current_resource),
            "blockingOperation": self._operation(state.blocking_operation),
            "activeOperation": self._operation(state.active_operation),
            "cleanupResources": [self._resource(item) for item in state.cleanup_resources],
            "latestObservation": self._observation(state.latest_observation),
            "historySummary": {
                "totalCount": state.history_summary.total_count,
                "latestOperationId": (
                    None if state.history_summary.latest_operation_id is None
                    else str(state.history_summary.latest_operation_id)
                ),
                "latestStatus": (
                    None if state.history_summary.latest_status is None
                    else state.history_summary.latest_status.value
                ),
            },
            "nextAllowedActions": [item.value for item in state.next_allowed_actions],
        }

    def operation(self, result: OperationSucceeded | OperationView) -> dict[str, object]:
        operation = result.operation if isinstance(result, OperationSucceeded) else result
        rendered = self._operation(operation)
        assert rendered is not None
        return rendered

    def history(self, result: HistorySucceeded) -> dict[str, object]:
        return {
            "items": [self._history_entry(item) for item in result.history.entries],
            "nextCursor": result.history.next_cursor,
            "hasMore": result.history.has_more,
        }

    def error(self, failure: ServiceFailed, *, error: BaseException | None = None):
        del error
        body = {
            "code": failure.code.value,
            "nextAllowedActions": [item.value for item in failure.next_allowed_actions],
        }
        if failure.errors:
            fields = []
            for item in failure.errors:
                field = _api_field_path(item.field)
                if field is not None:
                    fields.append({"field": field, "reason": item.reason})
            body["fields"] = fields or [{"field": "request", "reason": "invalid"}]
        return {"error": body}

    def _history_entry(self, entry: HistoryEntry) -> dict[str, object]:
        configuration = None
        if entry.configuration is not None:
            configuration = {
                "templateId": entry.configuration.reference.template_id,
                "templateVersion": entry.configuration.reference.version,
                "fields": [
                    {"displayName": item.display_name, "uri": item.uri}
                    for item in entry.configuration.fields
                ],
            }
        return {
            "operation": self._operation(entry.operation),
            "channelId": str(entry.channel_public_id),
            "channelLabel": entry.channel_label,
            "configuration": configuration,
            "transitions": [item.value for item in entry.transitions],
            "defaultRelation": entry.default_relation.value,
            "cleanupRelation": entry.cleanup_relation.value,
        }

    @staticmethod
    def _operation(operation: OperationView | None):
        if operation is None:
            return None
        return {
            "operationId": str(operation.operation_id),
            "kind": operation.kind.value,
            "status": operation.status.value,
            "stage": None if operation.stage is None else operation.stage.value,
            "result": operation.result.value,
            "subjectOperationId": (
                None if operation.subject_operation_id is None
                else str(operation.subject_operation_id)
            ),
            "targetResourceId": (
                None if operation.target_resource_id is None
                else str(operation.target_resource_id)
            ),
            "acceptedAt": _iso(operation.accepted_at),
            "completedAt": _iso(operation.completed_at),
            "nextAllowedActions": [item.value for item in operation.next_allowed_actions],
        }

    @staticmethod
    def _resource(resource: ManagedResourceView | None):
        if resource is None:
            return None
        return {
            "resourceId": str(resource.public_id),
            "originOperationId": str(resource.origin_operation_id),
            "lifecycle": resource.lifecycle.value,
            "imageDigest": resource.image_digest,
        }

    @staticmethod
    def _observation(observation: DefaultObservation | None):
        if observation is None:
            return None
        return {
            "kind": observation.kind.value,
            "observedAt": observation.observed_at.isoformat(),
            "fingerprint": observation.fingerprint,
            "managedResourceId": (
                None if observation.managed_resource_id is None
                else str(observation.managed_resource_id)
            ),
        }
