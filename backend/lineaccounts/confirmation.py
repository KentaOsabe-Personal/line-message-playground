"""全連携解除 snapshot 専用の短期署名境界。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from django.core import signing
from django.utils import timezone

from .repositories import UnlinkSnapshot


_PURPOSE = "account_unlink"
_VERSION = 1
_MAX_AGE_SECONDS = 5 * 60
_SALT = "lineaccounts.unlink-confirmation.v1"


class UnlinkConfirmation:
    def issue(self, snapshot: UnlinkSnapshot, now: datetime) -> str:
        self._require_aware(now)
        return signing.TimestampSigner(salt=_SALT).sign_object(
            {
                "purpose": _PURPOSE,
                "version": _VERSION,
                "issued_at": int(now.timestamp()),
                "fingerprint": self.fingerprint(snapshot),
            },
            compress=False,
        )

    def precheck(self, token: str, now: datetime) -> bool:
        payload = self._payload(token)
        return payload is not None and self._valid_payload(payload, now)

    def verify(self, token: str, snapshot: UnlinkSnapshot, now: datetime) -> bool:
        payload = self._payload(token)
        return (
            payload is not None
            and self._valid_payload(payload, now)
            and signing.constant_time_compare(
                payload["fingerprint"], self.fingerprint(snapshot)
            )
        )

    @staticmethod
    def fingerprint(snapshot: UnlinkSnapshot) -> str:
        fields = (
            _PURPOSE,
            str(snapshot.owner_slot),
            str(snapshot.identity_id),
            snapshot.display_name,
            *(str(value) for value in sorted(snapshot.recipient_ids, key=str)),
            *(str(value) for value in sorted(snapshot.channel_ids, key=str)),
            str(len(snapshot.recipient_ids)),
            "delivery_audit_retained=true",
        )
        canonical = b"".join(
            len(value.encode("utf-8")).to_bytes(8, "big") + value.encode("utf-8")
            for value in fields
        )
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _payload(token: str) -> dict[str, object] | None:
        if not isinstance(token, str) or not token:
            return None
        try:
            value = signing.TimestampSigner(salt=_SALT).unsign_object(token)
        except (signing.BadSignature, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(value, dict) or set(value) != {
            "purpose",
            "version",
            "issued_at",
            "fingerprint",
        }:
            return None
        return value

    @staticmethod
    def _valid_payload(payload: dict[str, object], now: datetime) -> bool:
        UnlinkConfirmation._require_aware(now)
        issued_at = payload.get("issued_at")
        fingerprint = payload.get("fingerprint")
        if (
            payload.get("purpose") != _PURPOSE
            or payload.get("version") != _VERSION
            or type(issued_at) is not int
            or not isinstance(fingerprint, str)
            or len(fingerprint) != 64
        ):
            return False
        age = int(now.timestamp()) - issued_at
        return 0 <= age <= _MAX_AGE_SECONDS

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if timezone.is_naive(value):
            raise ValueError("aware datetime required")
