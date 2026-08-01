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
        }

    def deleted(self, result: ChannelDeleteSucceeded) -> dict[str, object]:
        return {
            "channelId": str(result.channel_public_id),
            "label": result.label,
            "deleted": True,
        }

    def connection(
        self, channel_id, result: ConnectionCheckCompleted
    ) -> dict[str, object]:
        return {
            "channelId": str(channel_id),
            "status": result.status,
            "checkedAt": _datetime(result.checked_at),
            "scope": result.scope,
        }
