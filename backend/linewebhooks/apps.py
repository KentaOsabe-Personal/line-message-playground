from django.apps import AppConfig


class LineWebhooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "linewebhooks"

    def ready(self) -> None:
        from .container import initialize_webhook_ingress_service

        initialize_webhook_ingress_service()
