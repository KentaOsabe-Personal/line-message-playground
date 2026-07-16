import re


_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.ASCII)


def validate_public_host(value: str) -> str:
    if not value or len(value) > 253 or not value.isascii():
        raise ValueError("PUBLIC_HOST_INVALID")
    if value != value.strip() or any(
        character in value for character in ":/?#*@[]\\"
    ):
        raise ValueError("PUBLIC_HOST_INVALID")

    labels = value.split(".")
    if any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError("PUBLIC_HOST_INVALID")
    return value


def build_trusted_https_origin(host: str) -> str:
    return f"https://{validate_public_host(host)}"

