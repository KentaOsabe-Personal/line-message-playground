from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from django.core import signing

from .types import (
    ConfirmationAccepted,
    ConfirmationRejected,
    IssuedConfirmation,
    PreviewSnapshot,
)


class DefaultRichMenuConfirmation:
    SALT = "linerichmenus.confirmation.v1"
    _VERSION = 1
    _LIFETIME = timedelta(minutes=10)

    def __init__(self, *, purpose: str = "apply") -> None:
        if not isinstance(purpose, str) or not purpose:
            raise ValueError("confirmation purpose required")
        self._purpose = purpose

    def issue(self, snapshot: PreviewSnapshot, now: datetime) -> IssuedConfirmation:
        _require_aware(now)
        payload = {
            "purpose": self._purpose,
            "version": self._VERSION,
            "issuedAt": _canonical_time(now),
            "nonce": secrets.token_urlsafe(16),
            "fingerprint": _snapshot_fingerprint(snapshot),
        }
        token = signing.dumps(payload, salt=self.SALT, compress=False)
        return IssuedConfirmation(
            token=token,
            expires_at=now + self._LIFETIME,
            usage_digest=_usage_digest(token),
        )

    def verify(
        self,
        token: str,
        expected: PreviewSnapshot,
        now: datetime,
    ) -> ConfirmationAccepted | ConfirmationRejected:
        _require_aware(now)
        if not isinstance(token, str) or not token:
            return ConfirmationRejected(reason="preview_invalid")
        try:
            payload = signing.loads(token, salt=self.SALT)
            if not isinstance(payload, dict) or set(payload) != {
                "purpose",
                "version",
                "issuedAt",
                "nonce",
                "fingerprint",
            }:
                raise ValueError("invalid payload shape")
            if (
                payload["purpose"] != self._purpose
                or type(payload["version"]) is not int
                or payload["version"] != self._VERSION
                or not isinstance(payload["nonce"], str)
                or len(payload["nonce"]) < 22
                or not _is_sha256(payload["fingerprint"])
            ):
                raise ValueError("invalid payload values")
            issued_at = datetime.fromisoformat(payload["issuedAt"])
            _require_aware(issued_at)
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            return ConfirmationRejected(reason="preview_invalid")

        if issued_at > now:
            return ConfirmationRejected(reason="preview_invalid")
        if now - issued_at > self._LIFETIME:
            return ConfirmationRejected(reason="preview_expired")
        expected_fingerprint = _snapshot_fingerprint(expected)
        if not hmac.compare_digest(payload["fingerprint"], expected_fingerprint):
            return ConfirmationRejected(reason="preview_changed")
        return ConfirmationAccepted(usage_digest=_usage_digest(token))


def _snapshot_fingerprint(snapshot: PreviewSnapshot) -> str:
    if not isinstance(snapshot, PreviewSnapshot):
        raise ValueError("invalid preview snapshot")
    components = [
        snapshot.owner_identity.bytes,
        snapshot.provider_id.encode("utf-8"),
        snapshot.channel_public_id.bytes,
        _canonical_time(snapshot.channel_revision).encode("ascii"),
        snapshot.default_observation_fingerprint.encode("ascii"),
        snapshot.template.reference.template_id.encode("utf-8"),
        str(snapshot.template.reference.version).encode("ascii"),
        str(len(snapshot.template.fields)).encode("ascii"),
    ]
    for field in snapshot.template.fields:
        components.extend(
            (field.display_name.encode("utf-8"), field.uri.encode("utf-8"))
        )
    components.append(snapshot.pixel_digest.encode("ascii"))
    digest = sha256()
    for component in components:
        digest.update(len(component).to_bytes(8, "big"))
        digest.update(component)
    return digest.hexdigest()


def _usage_digest(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _canonical_time(value: datetime) -> str:
    _require_aware(value)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware datetime required")


def _is_sha256(value) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()
