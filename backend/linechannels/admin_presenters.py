from django.conf import settings
from django.urls import reverse

from config.public_origin import build_trusted_https_origin

from .admin_types import AdminChannelView, ChannelDeleteSucceeded, ConnectionCheckCompleted


def _datetime(value):
    if value is None:
        return None
    rendered = value.isoformat()
    return rendered[:-6] + "Z" if rendered.endswith("+00:00") else rendered


class AdminPresenter:
    def channel(self, channel: AdminChannelView) -> dict[str, object]:
        origin = build_trusted_https_origin(settings.PUBLIC_HOST)
        ingress_path = reverse(
            "linewebhooks:ingress",
            kwargs={"channel_public_key": str(channel.public_id)},
        )
        summary = channel.deactivation_summary
        return {
            "channelId": str(channel.public_id),
            "label": channel.label,
            "messagingApiChannelId": channel.messaging_api_channel_id,
            "botUserId": channel.bot_user_id,
            "providerId": channel.provider_id,
            "active": channel.is_active,
            "credentialsState": channel.credentials_state,
            "credentialsUpdatedAt": _datetime(channel.credentials_updated_at),
            "createdAt": _datetime(channel.created_at),
            "updatedAt": _datetime(channel.updated_at),
            "webhookUrl": f"{origin}{ingress_path}",
            "deactivationSummary": (
                {
                    "operationId": str(summary.operation_id),
                    "status": summary.status,
                    "reason": summary.safe_reason,
                    "updatedAt": _datetime(summary.updated_at),
                }
                if summary is not None
                else None
            ),
            "richMenuRefreshRequired": channel.rich_menu_refresh_required,
        }

    def deleted(self, result: ChannelDeleteSucceeded) -> dict[str, object]:
        return {
            "channelId": str(result.channel_public_id),
            "label": result.label,
            "deleted": True,
        }

    def connection(self, channel_id, result: ConnectionCheckCompleted) -> dict[str, object]:
        return {
            "channelId": str(channel_id),
            "status": result.status,
            "checkedAt": _datetime(result.checked_at),
            "scope": result.scope,
        }

    def deactivation(self, view) -> dict[str, object]:
        next_action = {
            "checking": "get_state",
            "unlinking": "get_state",
            "completed": "none",
        }.get(view.status)
        if view.status == "confirmation_required":
            next_action = {
                "external_default": "resolve_external_default_then_recheck",
                "cleanup_required": "complete_cleanup_then_recheck",
            }.get(view.safe_reason, "recheck")
        return {
            "channelId": str(view.channel_public_id),
            "channelActive": view.channel_active,
            "channelUpdatedAt": _datetime(view.channel_revision),
            "operationId": str(view.operation_id),
            "status": view.status,
            "reason": view.safe_reason,
            "subjectOperationId": (
                str(view.subject_rich_operation_id)
                if view.subject_rich_operation_id is not None
                else None
            ),
            "recoveryOperationId": (
                str(view.latest_recovery_operation_id)
                if view.latest_recovery_operation_id is not None
                else None
            ),
            "nextAction": next_action,
            "acceptedAt": _datetime(view.accepted_at),
            "updatedAt": _datetime(view.updated_at),
            "completedAt": _datetime(view.completed_at),
        }
