from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .runtime import CredentialKeyringConfigurationError, load_credential_keyring


class LineChannelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "linechannels"

    def ready(self) -> None:
        if settings.DEBUG:
            raise ImproperlyConfigured("line channel startup configuration is invalid")
        try:
            load_credential_keyring()
        except CredentialKeyringConfigurationError:
            raise ImproperlyConfigured(
                "line channel credential configuration is invalid"
            ) from None
