from __future__ import annotations

import hashlib
import secrets
from base64 import urlsafe_b64encode
from datetime import datetime
from typing import Callable

from .types import (
    ReceiptCapability,
    ReceiptCapabilityCandidate,
    ReceiptCommitment,
)


_CAPABILITY_ENTROPY_BYTES = 32


class ReceiptCapabilityFactory:
    """確認済み期限へ結び付く受取確認候補を生成する。"""

    def __init__(
        self,
        *,
        random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._random_bytes = random_bytes

    def create(
        self,
        confirmed_expires_at: datetime,
    ) -> ReceiptCapabilityCandidate:
        try:
            entropy = self._random_bytes(_CAPABILITY_ENTROPY_BYTES)
        except Exception:
            raise ValueError(
                "receipt capability generation failed"
            ) from None
        if (
            not isinstance(entropy, bytes)
            or len(entropy) != _CAPABILITY_ENTROPY_BYTES
        ):
            raise ValueError("receipt capability generation failed")

        raw = (
            urlsafe_b64encode(entropy)
            .rstrip(b"=")
            .decode("ascii")
        )
        digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
        return ReceiptCapabilityCandidate(
            capability=ReceiptCapability(raw),
            commitment=ReceiptCommitment(
                digest=digest,
                expires_at=confirmed_expires_at,
            ),
        )
