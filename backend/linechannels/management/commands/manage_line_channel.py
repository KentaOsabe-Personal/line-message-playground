import json

from django.core.management.base import BaseCommand, CommandError

from ...validators import BoundaryValidationError, validate_provider_id, validate_public_id

from ...types import (
    ChannelMutationFailed,
    ChannelMutationSucceeded,
    ManageLineChannelInputCancelled,
    ManageLineChannelInputCollected,
    ManageLineChannelInputInvalid,
    RegisterLineChannel,
    SetLineChannelActive,
    UpdateLineChannel,
)


def build_line_channel_service():
    from ...container import build_line_channel_service as build

    return build()


def build_manage_line_channel_prompts():
    from ...container import build_manage_line_channel_prompts as build

    return build()


class Command(BaseCommand):
    help = "Manage LINE channels without exposing credentials."

    def add_arguments(self, parser):
        parser.add_argument("--channel-public-id")
        parser.add_argument("--provider-id")

    def handle(self, *args, **options):
        try:
            service = build_line_channel_service()
            public_id = options.get("channel_public_id")
            provider_id = options.get("provider_id")
            if public_id is not None or provider_id is not None:
                if public_id is None or provider_id is None:
                    raise BoundaryValidationError()
                collected = ManageLineChannelInputCollected(
                    UpdateLineChannel(
                        channel_public_id=validate_public_id(public_id),
                        provider_id=validate_provider_id(provider_id),
                    )
                )
            else:
                prompts = build_manage_line_channel_prompts()
                collected = prompts.collect()
            if isinstance(collected, ManageLineChannelInputCancelled):
                self.stdout.write(self._json({"status": "cancelled"}))
                return
            if isinstance(collected, ManageLineChannelInputInvalid):
                self.stderr.write(self._json({"status": "invalid"}))
                return
            if not isinstance(collected, ManageLineChannelInputCollected):
                raise TypeError

            value = collected.value
            if isinstance(value, RegisterLineChannel):
                result = service.register(value)
            elif isinstance(value, UpdateLineChannel):
                result = service.update(value)
            elif isinstance(value, SetLineChannelActive):
                result = service.set_active(value.channel_public_id, value.active)
            else:
                raise TypeError

            if isinstance(result, ChannelMutationSucceeded):
                channel = result.channel
                self.stdout.write(
                    self._json(
                        {
                            "status": result.status,
                            "public_id": str(channel.public_id),
                            "messaging_api_channel_id": (
                                channel.messaging_api_channel_id
                            ),
                            "bot_user_id": channel.bot_user_id,
                            "label": channel.label,
                            "provider_id": channel.provider_id,
                            "is_active": channel.is_active,
                            "credentials_configured": (
                                channel.credentials_configured
                            ),
                            "created_at": channel.created_at.isoformat(),
                            "updated_at": channel.updated_at.isoformat(),
                        }
                    )
                )
                return
            if isinstance(result, ChannelMutationFailed):
                self.stderr.write(
                    self._json({"status": result.status, "code": result.code})
                )
                return
            raise TypeError
        except (EOFError, KeyboardInterrupt, BoundaryValidationError):
            raise CommandError("line channel management cancelled") from None
        except Exception:
            raise CommandError("line channel management failed") from None

    @staticmethod
    def _json(value: dict[str, object]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
