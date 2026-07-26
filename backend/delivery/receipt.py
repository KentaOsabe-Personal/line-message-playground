from __future__ import annotations

import hashlib
import secrets
from base64 import urlsafe_b64encode
from datetime import datetime
from typing import Callable

from lineinteractions.types import (
    ActionFailed,
    ActionNoChange,
    ActionOutcome,
    ActionRejected,
    ActionSucceeded,
    PostbackActionCommand,
)

from .repositories import AttemptRepository
from .types import (
    ConfirmReceiptCommand,
    ReceiptCapability,
    ReceiptCapabilityCandidate,
    ReceiptCommitment,
    ReceiptRecorded,
    ReceiptRejected,
    ReceiptUnchanged,
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


class ReceiptHandler:
    """検証済みpostbackを一件の受取確認へ縮約する。"""

    def __init__(
        self,
        *,
        attempt_repository: AttemptRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._attempt_repository = attempt_repository
        self._clock = clock

    def handle(self, command: PostbackActionCommand) -> ActionOutcome:
        if (
            not isinstance(command, PostbackActionCommand)
            or command.action_name != "delivery.received"
        ):
            return ActionRejected()

        try:
            raw_payload = command.payload.reveal_for_action()
            digest = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
            result = self._attempt_repository.confirm_receipt(
                ConfirmReceiptCommand(
                    capability_digest=digest,
                    channel_public_id=command.channel.channel_public_id,
                    recipient_public_id=command.user.recipient_public_id,
                    occurred_at=self._clock(),
                    webhook_event_id=command.webhook_event_id,
                )
            )
        except Exception:
            return ActionFailed()

        if isinstance(result, ReceiptRecorded):
            return ActionSucceeded()
        if isinstance(result, ReceiptUnchanged):
            return ActionNoChange()
        if isinstance(result, ReceiptRejected):
            return ActionRejected()
        return ActionFailed()
