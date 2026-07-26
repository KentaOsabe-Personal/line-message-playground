from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from string import hexdigits
from typing import Literal, TypeAlias
from uuid import UUID

from lineaccounts.types import LineSubject
from linechannels.types import AccessToken


TargetMode: TypeAlias = Literal["fixed_user", "linked_recipient"]
FriendshipState: TypeAlias = Literal["friend", "not_friend", "unknown"]
DeliveryStatus: TypeAlias = Literal[
    "processing", "succeeded", "failed", "unknown"
]
ReceiptStatus: TypeAlias = Literal[
    "not_requested", "pending", "confirmed", "expired"
]
TargetUnavailableReason: TypeAlias = Literal[
    "target_not_available",
    "channel_inactive",
    "recipient_disabled",
    "not_friend",
    "friendship_unknown",
    "no_deliverable_recipient",
]
RejectedPushFailureType: TypeAlias = Literal[
    "invalid_request",
    "authentication",
    "permission",
    "conflict",
    "rate_limited",
]
UnknownPushFailureType: TypeAlias = Literal[
    "service_unknown",
    "timeout_unknown",
    "response_unknown",
]
DeliveryFailureType: TypeAlias = Literal[
    "configuration",
    "invalid_request",
    "authentication",
    "permission",
    "conflict",
    "rate_limited",
    "service_unavailable",
    "service_unknown",
    "timeout_unknown",
    "response_unknown",
    "processing_expired",
    "target_changed",
    "storage_unavailable",
    "unexpected",
]
ReceiptRejectionReason: TypeAlias = Literal[
    "unmatched",
    "expired",
    "target_mismatch",
    "not_requested",
    "delivery_failed",
    "invalid",
]

_FRIENDSHIP_STATES = frozenset(("friend", "not_friend", "unknown"))
_DELIVERY_STATUSES = frozenset(
    ("processing", "succeeded", "failed", "unknown")
)
_RECEIPT_STATUSES = frozenset(
    ("not_requested", "pending", "confirmed", "expired")
)
_TARGET_UNAVAILABLE_REASONS = frozenset(
    (
        "target_not_available",
        "channel_inactive",
        "recipient_disabled",
        "not_friend",
        "friendship_unknown",
        "no_deliverable_recipient",
    )
)
_REJECTED_PUSH_FAILURE_TYPES = frozenset(
    (
        "invalid_request",
        "authentication",
        "permission",
        "conflict",
        "rate_limited",
    )
)
_UNKNOWN_PUSH_FAILURE_TYPES = frozenset(
    ("service_unknown", "timeout_unknown", "response_unknown")
)
_DELIVERY_FAILURE_TYPES = frozenset(
    (
        "configuration",
        "invalid_request",
        "authentication",
        "permission",
        "conflict",
        "rate_limited",
        "service_unavailable",
        "service_unknown",
        "timeout_unknown",
        "response_unknown",
        "processing_expired",
        "target_changed",
        "storage_unavailable",
        "unexpected",
    )
)
_RECEIPT_REJECTION_REASONS = frozenset(
    (
        "unmatched",
        "expired",
        "target_mismatch",
        "not_requested",
        "delivery_failed",
        "invalid",
    )
)


class _SerializationDisabled:
    __slots__ = ()

    def __reduce__(self) -> object:
        raise TypeError("serialization is disabled")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("serialization is disabled")


class ReceiptCapability(_SerializationDisabled):
    """Gatewayだけが取り出せる、一時的な受取確認capability。"""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("invalid receipt capability")
        object.__setattr__(
            self,
            "_ReceiptCapability__value",
            value,
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("receipt capabilities are immutable")

    def __repr__(self) -> str:
        return "<ReceiptCapability redacted>"

    __str__ = __repr__

    def reveal_for_push_action(self) -> str:
        return self.__value


@dataclass(frozen=True, slots=True)
class OwnerPrincipal:
    slot: int

    def __post_init__(self) -> None:
        if type(self.slot) is not int or self.slot <= 0:
            raise ValueError("invalid owner principal slot")


@dataclass(frozen=True, slots=True)
class OwnerIdentitySnapshot:
    public_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.public_id, UUID):
            raise ValueError("invalid owner identity")


@dataclass(frozen=True, slots=True)
class TargetRevision:
    digest: str

    def __post_init__(self) -> None:
        _validate_sha256(self.digest, "target revision")


@dataclass(frozen=True, slots=True)
class RequestFingerprint:
    digest: str

    def __post_init__(self) -> None:
        _validate_sha256(self.digest, "request fingerprint")


class MessageSnapshot(_SerializationDisabled):
    __slots__ = ("__subject", "__body", "__formatted_text", "__fingerprint")

    def __init__(
        self,
        *,
        subject: str,
        body: str,
        formatted_text: str,
        fingerprint: str,
    ) -> None:
        if not all(
            isinstance(value, str)
            for value in (subject, body, formatted_text)
        ):
            raise ValueError("invalid message snapshot")
        _validate_sha256(fingerprint, "message fingerprint")
        object.__setattr__(self, "_MessageSnapshot__subject", subject)
        object.__setattr__(self, "_MessageSnapshot__body", body)
        object.__setattr__(
            self,
            "_MessageSnapshot__formatted_text",
            formatted_text,
        )
        object.__setattr__(
            self,
            "_MessageSnapshot__fingerprint",
            fingerprint,
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("message snapshots are immutable")

    @property
    def subject(self) -> str:
        return self.__subject

    @property
    def body(self) -> str:
        return self.__body

    @property
    def formatted_text(self) -> str:
        return self.__formatted_text

    @property
    def fingerprint(self) -> str:
        return self.__fingerprint

    def __repr__(self) -> str:
        return (
            "<MessageSnapshot "
            f"fingerprint={self.fingerprint} content=redacted>"
        )

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MessageSnapshot):
            return NotImplemented
        return (
            self.subject,
            self.body,
            self.formatted_text,
            self.fingerprint,
        ) == (
            other.subject,
            other.body,
            other.formatted_text,
            other.fingerprint,
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.subject,
                self.body,
                self.formatted_text,
                self.fingerprint,
            )
        )


@dataclass(frozen=True, slots=True)
class FixedTargetSnapshot:
    mode: Literal["fixed_user"] = "fixed_user"

    def __post_init__(self) -> None:
        if self.mode != "fixed_user":
            raise ValueError("invalid fixed target mode")


@dataclass(frozen=True, slots=True)
class LinkedTargetSnapshot:
    channel_public_id: UUID
    channel_label: str
    recipient_public_id: UUID
    channel_active: bool
    recipient_enabled: bool
    friendship_state: FriendshipState
    mode: Literal["linked_recipient"] = "linked_recipient"

    def __post_init__(self) -> None:
        if self.mode != "linked_recipient":
            raise ValueError("invalid linked target mode")
        if not isinstance(self.channel_public_id, UUID):
            raise ValueError("invalid channel public ID")
        if not isinstance(self.recipient_public_id, UUID):
            raise ValueError("invalid recipient public ID")
        if not isinstance(self.channel_label, str) or not self.channel_label:
            raise ValueError("invalid channel label")
        if type(self.channel_active) is not bool:
            raise ValueError("invalid channel active state")
        if type(self.recipient_enabled) is not bool:
            raise ValueError("invalid recipient enabled state")
        if self.friendship_state not in _FRIENDSHIP_STATES:
            raise ValueError("invalid friendship state")


TargetSnapshot: TypeAlias = FixedTargetSnapshot | LinkedTargetSnapshot


@dataclass(frozen=True, slots=True, repr=False)
class LiveDeliveryTarget(_SerializationDisabled):
    owner_identity: OwnerIdentitySnapshot
    provider_id: str
    snapshot: LinkedTargetSnapshot
    revision: TargetRevision
    subject: LineSubject
    delivery_available: bool

    def __post_init__(self) -> None:
        if not isinstance(self.owner_identity, OwnerIdentitySnapshot):
            raise ValueError("invalid owner identity")
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("invalid provider")
        if not isinstance(self.snapshot, LinkedTargetSnapshot):
            raise ValueError("invalid linked target snapshot")
        if not isinstance(self.revision, TargetRevision):
            raise ValueError("invalid target revision")
        if not isinstance(self.subject, LineSubject):
            raise ValueError("invalid LINE subject")
        if type(self.delivery_available) is not bool:
            raise ValueError("invalid delivery availability")
        expected = (
            self.snapshot.channel_active
            and self.snapshot.recipient_enabled
            and self.snapshot.friendship_state == "friend"
        )
        if self.delivery_available != expected:
            raise ValueError("delivery availability does not match target state")

    def __repr__(self) -> str:
        return (
            "<LiveDeliveryTarget "
            f"channel_public_id={self.snapshot.channel_public_id} "
            f"recipient_public_id={self.snapshot.recipient_public_id} "
            f"delivery_available={self.delivery_available} "
            "subject=redacted>"
        )


@dataclass(frozen=True, slots=True)
class DeliveryChannelChoice:
    channel_public_id: UUID
    label: str
    active: bool
    available: bool
    unavailable_reason: TargetUnavailableReason | None

    def __post_init__(self) -> None:
        if not isinstance(self.channel_public_id, UUID):
            raise ValueError("invalid channel choice")
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("invalid channel label")
        if type(self.active) is not bool:
            raise ValueError("invalid channel active state")
        _validate_availability(
            self.available,
            self.unavailable_reason,
        )


@dataclass(frozen=True, slots=True)
class DeliveryRecipientChoice:
    recipient_public_id: UUID
    display_name: str
    enabled: bool
    friendship_state: FriendshipState
    available: bool
    unavailable_reason: TargetUnavailableReason | None

    def __post_init__(self) -> None:
        if not isinstance(self.recipient_public_id, UUID):
            raise ValueError("invalid recipient choice")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("invalid recipient display name")
        if type(self.enabled) is not bool:
            raise ValueError("invalid recipient enabled state")
        if self.friendship_state not in _FRIENDSHIP_STATES:
            raise ValueError("invalid friendship state")
        _validate_availability(
            self.available,
            self.unavailable_reason,
        )


@dataclass(frozen=True, slots=True)
class TargetUnavailable:
    reason: TargetUnavailableReason = "target_not_available"

    def __post_init__(self) -> None:
        if self.reason not in _TARGET_UNAVAILABLE_REASONS:
            raise ValueError("invalid target unavailable reason")


@dataclass(frozen=True, slots=True)
class ConfirmationSnapshot:
    owner: OwnerPrincipal
    owner_identity: OwnerIdentitySnapshot
    channel_public_id: UUID
    recipient_public_id: UUID
    target_revision: TargetRevision
    message_fingerprint: str
    receipt_requested: bool
    receipt_expires_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.owner, OwnerPrincipal):
            raise ValueError("invalid owner principal")
        if not isinstance(self.owner_identity, OwnerIdentitySnapshot):
            raise ValueError("invalid owner identity")
        if not isinstance(self.channel_public_id, UUID):
            raise ValueError("invalid channel public ID")
        if not isinstance(self.recipient_public_id, UUID):
            raise ValueError("invalid recipient public ID")
        if not isinstance(self.target_revision, TargetRevision):
            raise ValueError("invalid target revision")
        _validate_sha256(self.message_fingerprint, "message fingerprint")
        _validate_receipt_expiry(
            self.receipt_requested,
            self.receipt_expires_at,
        )


@dataclass(frozen=True, slots=True)
class ReceiptCommitment:
    digest: str
    expires_at: datetime

    def __post_init__(self) -> None:
        _validate_sha256(self.digest, "receipt commitment")
        _validate_aware_datetime(self.expires_at, "receipt expiry")


@dataclass(frozen=True, slots=True, repr=False)
class ReceiptCapabilityCandidate(_SerializationDisabled):
    capability: ReceiptCapability
    commitment: ReceiptCommitment

    def __post_init__(self) -> None:
        if not isinstance(self.capability, ReceiptCapability):
            raise ValueError("invalid receipt capability")
        if not isinstance(self.commitment, ReceiptCommitment):
            raise ValueError("invalid receipt commitment")

    def __repr__(self) -> str:
        return (
            "<ReceiptCapabilityCandidate "
            f"digest={self.commitment.digest} "
            f"expires_at={self.commitment.expires_at.isoformat()} "
            "capability=redacted>"
        )


@dataclass(frozen=True, slots=True)
class AcceptedDeliveryCommand:
    operation_id: UUID
    owner: OwnerPrincipal
    owner_identity: OwnerIdentitySnapshot
    target: LinkedTargetSnapshot
    message: MessageSnapshot
    request_fingerprint: RequestFingerprint
    receipt_commitment: ReceiptCommitment | None

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, UUID):
            raise ValueError("invalid operation ID")
        if not isinstance(self.owner, OwnerPrincipal):
            raise ValueError("invalid owner principal")
        if not isinstance(self.owner_identity, OwnerIdentitySnapshot):
            raise ValueError("invalid owner identity")
        if not isinstance(self.target, LinkedTargetSnapshot):
            raise ValueError("invalid linked target")
        if not isinstance(self.message, MessageSnapshot):
            raise ValueError("invalid message snapshot")
        if not isinstance(self.request_fingerprint, RequestFingerprint):
            raise ValueError("invalid request fingerprint")
        if self.receipt_commitment is not None and not isinstance(
            self.receipt_commitment,
            ReceiptCommitment,
        ):
            raise ValueError("invalid receipt commitment")


@dataclass(frozen=True, slots=True, repr=False)
class PushLinkedRecipientCommand(_SerializationDisabled):
    operation_id: UUID
    access_token: AccessToken
    subject: LineSubject
    text: str
    receipt_capability: ReceiptCapability | None

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, UUID):
            raise ValueError("invalid operation ID")
        if not isinstance(self.access_token, AccessToken):
            raise ValueError("invalid access token")
        if not isinstance(self.subject, LineSubject):
            raise ValueError("invalid LINE subject")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("invalid formatted text")
        if self.receipt_capability is not None and not isinstance(
            self.receipt_capability,
            ReceiptCapability,
        ):
            raise ValueError("invalid receipt capability")

    def __repr__(self) -> str:
        return (
            "<PushLinkedRecipientCommand "
            f"operation_id={self.operation_id} "
            f"receipt_requested={self.receipt_capability is not None} "
            "access_token=redacted subject=redacted text=redacted>"
        )


@dataclass(frozen=True, slots=True)
class LinePushAccepted:
    request_id: str | None
    accepted_request_id: str | None
    status: Literal["accepted"] = "accepted"

    def __post_init__(self) -> None:
        if self.status != "accepted":
            raise ValueError("invalid accepted push status")
        for request_id in (self.request_id, self.accepted_request_id):
            if request_id is not None and (
                not isinstance(request_id, str) or not request_id
            ):
                raise ValueError("invalid LINE request ID")


@dataclass(frozen=True, slots=True)
class LinePushRejected:
    failure_type: RejectedPushFailureType
    status: Literal["rejected"] = "rejected"

    def __post_init__(self) -> None:
        if self.status != "rejected":
            raise ValueError("invalid rejected push status")
        if self.failure_type not in _REJECTED_PUSH_FAILURE_TYPES:
            raise ValueError("invalid rejected push failure type")


@dataclass(frozen=True, slots=True)
class LinePushUnknown:
    failure_type: UnknownPushFailureType
    status: Literal["unknown"] = "unknown"

    def __post_init__(self) -> None:
        if self.status != "unknown":
            raise ValueError("invalid unknown push status")
        if self.failure_type not in _UNKNOWN_PUSH_FAILURE_TYPES:
            raise ValueError("invalid unknown push failure type")


LinePushResult: TypeAlias = (
    LinePushAccepted | LinePushRejected | LinePushUnknown
)


@dataclass(frozen=True, slots=True, repr=False)
class DeliverySnapshot(_SerializationDisabled):
    operation_id: UUID
    owner: OwnerPrincipal
    owner_identity: OwnerIdentitySnapshot | None
    target: TargetSnapshot
    message: MessageSnapshot
    status: DeliveryStatus
    accepted_at: datetime
    completed_at: datetime | None
    line_request_id: str | None
    line_accepted_request_id: str | None
    failure: DeliveryFailureType | None
    receipt_status: ReceiptStatus
    receipt_expires_at: datetime | None
    receipt_confirmed_at: datetime | None
    receipt_webhook_event_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, UUID):
            raise ValueError("invalid operation ID")
        if not isinstance(self.owner, OwnerPrincipal):
            raise ValueError("invalid owner principal")
        if not isinstance(self.target, (FixedTargetSnapshot, LinkedTargetSnapshot)):
            raise ValueError("invalid target snapshot")
        if isinstance(self.target, LinkedTargetSnapshot):
            if not isinstance(self.owner_identity, OwnerIdentitySnapshot):
                raise ValueError("linked target requires owner identity")
        elif self.owner_identity is not None and not isinstance(
            self.owner_identity,
            OwnerIdentitySnapshot,
        ):
            raise ValueError("invalid owner identity")
        if not isinstance(self.message, MessageSnapshot):
            raise ValueError("invalid message snapshot")
        if self.status not in _DELIVERY_STATUSES:
            raise ValueError("invalid delivery status")
        _validate_aware_datetime(self.accepted_at, "accepted at")
        if self.status == "processing":
            if self.completed_at is not None or self.failure is not None:
                raise ValueError("processing delivery cannot be completed")
        else:
            _validate_aware_datetime(self.completed_at, "completed at")
        if self.status in ("failed", "unknown"):
            if self.failure is None:
                raise ValueError("unsuccessful delivery requires failure type")
            _validate_failure_type(self.failure)
        elif self.failure is not None:
            raise ValueError("successful delivery cannot have failure type")
        for request_id in (
            self.line_request_id,
            self.line_accepted_request_id,
        ):
            if request_id is not None and (
                not isinstance(request_id, str) or not request_id
            ):
                raise ValueError("invalid LINE request ID")
        _validate_receipt_snapshot(
            self.receipt_status,
            self.receipt_expires_at,
            self.receipt_confirmed_at,
            self.receipt_webhook_event_id,
        )

    def __repr__(self) -> str:
        return (
            "<DeliverySnapshot "
            f"operation_id={self.operation_id} "
            f"target_mode={self.target.mode} status={self.status} "
            f"receipt_status={self.receipt_status} "
            "message=redacted>"
        )


@dataclass(frozen=True, slots=True)
class AttemptAccepted:
    attempt_id: int
    snapshot: DeliverySnapshot
    status: Literal["accepted"] = "accepted"

    def __post_init__(self) -> None:
        if type(self.attempt_id) is not int or self.attempt_id <= 0:
            raise ValueError("invalid attempt ID")
        if not isinstance(self.snapshot, DeliverySnapshot):
            raise ValueError("invalid delivery snapshot")
        if self.status != "accepted":
            raise ValueError("invalid attempt status")


@dataclass(frozen=True, slots=True)
class ExistingAttempt:
    snapshot: DeliverySnapshot
    status: Literal["existing"] = "existing"

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, DeliverySnapshot):
            raise ValueError("invalid delivery snapshot")
        if self.status != "existing":
            raise ValueError("invalid attempt status")


@dataclass(frozen=True, slots=True)
class AttemptConflict:
    status: Literal["conflict"] = "conflict"

    def __post_init__(self) -> None:
        if self.status != "conflict":
            raise ValueError("invalid attempt status")


AttemptAcceptResult: TypeAlias = (
    AttemptAccepted | ExistingAttempt | AttemptConflict
)


@dataclass(frozen=True, slots=True)
class ConfirmReceiptCommand:
    capability_digest: str
    channel_public_id: UUID
    recipient_public_id: UUID
    occurred_at: datetime
    webhook_event_id: str

    def __post_init__(self) -> None:
        _validate_sha256(self.capability_digest, "receipt capability digest")
        if not isinstance(self.channel_public_id, UUID):
            raise ValueError("invalid channel public ID")
        if not isinstance(self.recipient_public_id, UUID):
            raise ValueError("invalid recipient public ID")
        _validate_aware_datetime(self.occurred_at, "receipt occurred at")
        if not isinstance(self.webhook_event_id, str) or not self.webhook_event_id:
            raise ValueError("invalid webhook event ID")


@dataclass(frozen=True, slots=True)
class ReceiptRecorded:
    snapshot: DeliverySnapshot
    status: Literal["recorded"] = "recorded"

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, DeliverySnapshot):
            raise ValueError("invalid delivery snapshot")
        if self.status != "recorded":
            raise ValueError("invalid receipt result status")


@dataclass(frozen=True, slots=True)
class ReceiptUnchanged:
    snapshot: DeliverySnapshot
    status: Literal["unchanged"] = "unchanged"

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, DeliverySnapshot):
            raise ValueError("invalid delivery snapshot")
        if self.status != "unchanged":
            raise ValueError("invalid receipt result status")


@dataclass(frozen=True, slots=True)
class ReceiptRejected:
    reason: ReceiptRejectionReason
    status: Literal["rejected"] = "rejected"

    def __post_init__(self) -> None:
        if self.status != "rejected":
            raise ValueError("invalid receipt result status")
        if self.reason not in _RECEIPT_REJECTION_REASONS:
            raise ValueError("invalid receipt rejection reason")


ReceiptResult: TypeAlias = ReceiptRecorded | ReceiptUnchanged | ReceiptRejected


def _validate_sha256(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in hexdigits for character in value)
        or value.lower() != value
    ):
        raise ValueError(f"invalid {label}")


def _validate_aware_datetime(
    value: datetime | None,
    label: str,
) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"invalid {label}")


def _validate_receipt_expiry(
    requested: bool,
    expires_at: datetime | None,
) -> None:
    if type(requested) is not bool:
        raise ValueError("invalid receipt requested flag")
    if requested:
        _validate_aware_datetime(expires_at, "receipt expiry")
    elif expires_at is not None:
        raise ValueError("receipt expiry requires requested receipt")


def _validate_availability(
    available: bool,
    reason: TargetUnavailableReason | None,
) -> None:
    if type(available) is not bool:
        raise ValueError("invalid target availability")
    if available and reason is not None:
        raise ValueError("available target cannot have unavailable reason")
    if not available and reason not in _TARGET_UNAVAILABLE_REASONS:
        raise ValueError("unavailable target requires safe reason")


def _validate_failure_type(failure_type: str) -> None:
    if failure_type not in _DELIVERY_FAILURE_TYPES:
        raise ValueError("invalid delivery failure type")


def _validate_receipt_snapshot(
    status: ReceiptStatus,
    expires_at: datetime | None,
    confirmed_at: datetime | None,
    webhook_event_id: str | None,
) -> None:
    if status not in _RECEIPT_STATUSES:
        raise ValueError("invalid receipt status")
    if status == "not_requested":
        if any(
            value is not None
            for value in (expires_at, confirmed_at, webhook_event_id)
        ):
            raise ValueError("non-requested receipt cannot have state")
        return
    _validate_aware_datetime(expires_at, "receipt expiry")
    if status == "confirmed":
        _validate_aware_datetime(confirmed_at, "receipt confirmed at")
        if not isinstance(webhook_event_id, str) or not webhook_event_id:
            raise ValueError("confirmed receipt requires event ID")
    elif confirmed_at is not None or webhook_event_id is not None:
        raise ValueError("unconfirmed receipt cannot have confirmation fields")
