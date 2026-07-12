from django.core import signing

from .formatters import FORMATTER_VERSION


CONFIRMATION_SALT = "delivery.confirmation.v1"


class ConfirmationError(ValueError):
    pass


class ConfirmationTokenService:
    def issue(self, message):
        return signing.dumps(
            {"v": message.formatter_version, "fp": message.fingerprint},
            salt=CONFIRMATION_SALT,
            compress=True,
        )

    def verify(self, token, message):
        try:
            payload = signing.loads(token, salt=CONFIRMATION_SALT)
        except (signing.BadSignature, TypeError, ValueError) as error:
            raise ConfirmationError("confirmation_invalid") from error
        if message.formatter_version != FORMATTER_VERSION or payload != {
            "v": message.formatter_version,
            "fp": message.fingerprint,
        }:
            raise ConfirmationError("confirmation_mismatch")

    def decode_for_test(self, token):
        return signing.loads(token, salt=CONFIRMATION_SALT)
