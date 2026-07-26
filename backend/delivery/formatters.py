import hashlib
import json
from dataclasses import dataclass

from delivery.types import MessageSnapshot


FORMATTER_VERSION = 1
MAX_UTF16_CODE_UNITS = 5_000


class MessageValidationError(ValueError):
    def __init__(self, code, *, field=None):
        self.code = code
        self.field = field
        super().__init__(code)


@dataclass(frozen=True)
class FormattedMessage:
    subject: str
    body: str
    formatted_text: str
    fingerprint: str
    formatter_version: int


def count_utf16_code_units(text):
    try:
        return len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as error:
        raise MessageValidationError("invalid_unicode") from error


def format_message(subject, body):
    if not isinstance(subject, str) or not subject.strip():
        raise MessageValidationError("blank", field="subject")
    if not isinstance(body, str) or not body.strip():
        raise MessageValidationError("blank", field="body")

    formatted_text = f"【{subject}】\n\n{body}"
    if count_utf16_code_units(formatted_text) > MAX_UTF16_CODE_UNITS:
        raise MessageValidationError("message_too_long")

    serialized = json.dumps(
        [FORMATTER_VERSION, subject, body, formatted_text],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(serialized).hexdigest()
    return FormattedMessage(
        subject=subject,
        body=body,
        formatted_text=formatted_text,
        fingerprint=fingerprint,
        formatter_version=FORMATTER_VERSION,
    )


def format_message_snapshot(subject: str, body: str) -> MessageSnapshot:
    """既存のcanonical formatterで新しい配信経路のsnapshotを生成する。"""

    message = format_message(subject, body)
    return MessageSnapshot(
        subject=message.subject,
        body=message.body,
        formatted_text=message.formatted_text,
        fingerprint=message.fingerprint,
    )
