from django.apps import AppConfig
from django.conf import settings


class LineAccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "lineaccounts"

    def ready(self):
        from .runtime import initialize_line_account_runtime

        initialize_line_account_runtime(settings.SECRET_KEY)

