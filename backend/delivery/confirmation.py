from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, TypeAlias

from django.core import signing
from django.utils import timezone as django_timezone

from .formatters import FORMATTER_VERSION
from .types import ConfirmationSnapshot


CONFIRMATION_SALT = "delivery.confirmation.v1"
LINKED_CONFIRMATION_SALT = "delivery.confirmation.snapshot.v1"
CONFIRMATION_MAX_AGE = timedelta(minutes=10)
RECEIPT_MAX_AGE = timedelta(hours=24)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_VALID_CONFIRMATION_REJECTIONS = frozenset(
    ("invalid", "expired", "mismatch")
)


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


ConfirmationRejectionReason: TypeAlias = Literal[
    "invalid", "expired", "mismatch"
]


@dataclass(frozen=True, slots=True)
class IssuedConfirmation:
    token: str
    receipt_expires_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.token, str) or not self.token:
            raise ValueError("invalid confirmation token")
        if self.receipt_expires_at is not None:
            _validate_aware_datetime(
                self.receipt_expires_at,
                "receipt expiry",
            )


@dataclass(frozen=True, slots=True)
class ConfirmationVerified:
    snapshot: ConfirmationSnapshot
    status: Literal["verified"] = "verified"

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, ConfirmationSnapshot):
            raise ValueError("invalid confirmation snapshot")
        if self.status != "verified":
            raise ValueError("invalid confirmation verification status")


@dataclass(frozen=True, slots=True)
class ConfirmationRejected:
    reason: ConfirmationRejectionReason
    status: Literal["rejected"] = "rejected"

    def __post_init__(self) -> None:
        if self.reason not in _VALID_CONFIRMATION_REJECTIONS:
            raise ValueError("invalid confirmation rejection reason")
        if self.status != "rejected":
            raise ValueError("invalid confirmation rejection status")


ConfirmationVerification: TypeAlias = (
    ConfirmationVerified | ConfirmationRejected
)


class _ClockTimestampSigner(signing.TimestampSigner):
    """Django TimestampSignerを注入clockと同じ時間領域で動かす。"""

    def __init__(
        self,
        *,
        salt: str,
        clock: Callable[[], datetime],
    ) -> None:
        super().__init__(salt=salt)
        self._clock = clock

    def timestamp(self) -> str:
        return signing.b62_encode(_clock_microseconds(self._clock))

    def unsign(
        self,
        value: str,
        max_age: timedelta | float | int | None = None,
    ) -> str:
        result = signing.Signer.unsign(self, value)
        unsigned, timestamp = result.rsplit(self.sep, 1)
        signed_at = signing.b62_decode(timestamp)
        if max_age is not None:
            max_age_seconds = (
                max_age.total_seconds()
                if isinstance(max_age, timedelta)
                else max_age
            )
            age_microseconds = (
                _clock_microseconds(self._clock) - signed_at
            )
            max_age_microseconds = max_age_seconds * 1_000_000
            if age_microseconds > max_age_microseconds:
                raise signing.SignatureExpired(
                    "Signature age "
                    f"{age_microseconds / 1_000_000} > "
                    f"{max_age_seconds} seconds"
                )
        return unsigned


class ConfirmationService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = django_timezone.now,
    ) -> None:
        self._clock = clock
        self._signer = _ClockTimestampSigner(
            salt=LINKED_CONFIRMATION_SALT,
            clock=clock,
        )

    def receipt_expires_at(
        self,
        receipt_requested: bool,
    ) -> datetime | None:
        if type(receipt_requested) is not bool:
            raise ValueError("invalid receipt requested flag")
        if not receipt_requested:
            return None
        return _aware_now(self._clock) + RECEIPT_MAX_AGE

    def issue(
        self,
        snapshot: ConfirmationSnapshot,
    ) -> IssuedConfirmation:
        if not isinstance(snapshot, ConfirmationSnapshot):
            raise ValueError("invalid confirmation snapshot")
        self._validate_receipt_window(snapshot)
        token = self._signer.sign_object(
            _payload_from_snapshot(snapshot),
            compress=True,
        )
        return IssuedConfirmation(
            token=token,
            receipt_expires_at=snapshot.receipt_expires_at,
        )

    def verify(
        self,
        token: str,
        expected: ConfirmationSnapshot,
    ) -> ConfirmationVerification:
        if not isinstance(token, str) or not isinstance(
            expected, ConfirmationSnapshot
        ):
            return ConfirmationRejected("invalid")
        try:
            payload = self._signer.unsign_object(
                token,
                max_age=CONFIRMATION_MAX_AGE,
            )
        except signing.SignatureExpired:
            return ConfirmationRejected("expired")
        except (signing.BadSignature, TypeError, ValueError):
            return ConfirmationRejected("invalid")
        if payload != _payload_from_snapshot(expected):
            return ConfirmationRejected("mismatch")
        return ConfirmationVerified(expected)

    def decode_for_test(self, token: str) -> object:
        return self._signer.unsign_object(token)

    def _validate_receipt_window(
        self,
        snapshot: ConfirmationSnapshot,
    ) -> None:
        if not snapshot.receipt_requested:
            return
        now = _aware_now(self._clock)
        expires_at = snapshot.receipt_expires_at
        if (
            expires_at is None
            or expires_at <= now
            or expires_at > now + RECEIPT_MAX_AGE
        ):
            raise ValueError("invalid receipt expiry")


def _clock_microseconds(clock: Callable[[], datetime]) -> int:
    elapsed = _aware_now(clock).astimezone(timezone.utc) - _UNIX_EPOCH
    return (
        (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000
        + elapsed.microseconds
    )


def _aware_now(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    _validate_aware_datetime(now, "clock value")
    return now


def _validate_aware_datetime(value: object, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"invalid {label}")


def _payload_from_snapshot(
    snapshot: ConfirmationSnapshot,
) -> dict[str, object]:
    return {
        "v": 1,
        "owner": snapshot.owner.slot,
        "identity": str(snapshot.owner_identity.public_id),
        "channel": str(snapshot.channel_public_id),
        "recipient": str(snapshot.recipient_public_id),
        "target_revision": snapshot.target_revision.digest,
        "message_fingerprint": snapshot.message_fingerprint,
        "receipt_requested": snapshot.receipt_requested,
        "receipt_expires_at": _serialize_datetime(
            snapshot.receipt_expires_at
        ),
    }


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
