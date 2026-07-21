from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

from django.db import DatabaseError, OperationalError, transaction
from django.utils import timezone

from linefriendships.types import (
    FriendshipState,
    LockedRecipientProjection,
    ProjectionTarget,
    ProjectionTargetMissing,
)

from .models import DeliveryRecipient, OwnerAccount
from .repositories import AccountPersistenceError, AccountRepositoryProgrammingError
from .types import LineSubject


class DjangoAccountProjectionRepository:
    _RETRYABLE_DATABASE_CODES = frozenset((1205, 1213))

    def __init__(self, using: str = "default") -> None:
        self.using = using

    def lock_target(
        self,
        *,
        channel_public_id: UUID,
        provider_id: str,
        subject: LineSubject,
    ) -> ProjectionTarget:
        self._require_transaction()
        with self._translate_database_errors():
            owner = (
                OwnerAccount.objects.using(self.using)
                .select_for_update()
                .select_related("identity")
                .get(slot=1)
            )
            identity = owner.identity
            if (
                owner.state != OwnerAccount.State.ACTIVE
                or identity is None
                or identity.provider_id != provider_id
                or identity.subject != subject.reveal_for_identity_binding()
            ):
                return ProjectionTargetMissing()

            recipient = (
                DeliveryRecipient.objects.using(self.using)
                .select_for_update()
                .filter(
                    identity=identity,
                    line_channel__public_id=channel_public_id,
                    line_channel__provider_id=provider_id,
                )
                .first()
            )
            if recipient is None:
                return ProjectionTargetMissing()
            return LockedRecipientProjection(
                recipient_public_id=recipient.public_id,
                registered_at=recipient.created_at,
                friendship_state=recipient.friendship_state,
                last_occurred_at_ms=(
                    recipient.last_friendship_event_occurred_at_ms
                ),
                last_webhook_event_id=(
                    recipient.last_friendship_webhook_event_id
                ),
            )

    def apply_locked(
        self,
        target: LockedRecipientProjection,
        *,
        friendship_state: FriendshipState,
        occurred_at_ms: int,
        webhook_event_id: str,
    ) -> None:
        self._require_transaction()
        if (
            not isinstance(target, LockedRecipientProjection)
            or friendship_state not in DeliveryRecipient.FriendshipState.values
            or friendship_state == DeliveryRecipient.FriendshipState.UNKNOWN
            or type(occurred_at_ms) is not int
            or occurred_at_ms < 0
            or not isinstance(webhook_event_id, str)
            or not webhook_event_id
        ):
            raise AccountRepositoryProgrammingError("invalid_command")

        with self._translate_database_errors():
            recipient = (
                DeliveryRecipient.objects.using(self.using)
                .select_for_update()
                .get(public_id=target.recipient_public_id)
            )
            recipient.friendship_state = friendship_state
            recipient.last_friendship_event_occurred_at_ms = occurred_at_ms
            recipient.last_friendship_webhook_event_id = webhook_event_id
            recipient.updated_at = timezone.now()
            recipient.save(
                using=self.using,
                update_fields=(
                    "friendship_state",
                    "last_friendship_event_occurred_at_ms",
                    "last_friendship_webhook_event_id",
                    "updated_at",
                ),
            )

    def _require_transaction(self) -> None:
        if not transaction.get_connection(self.using).in_atomic_block:
            raise AccountRepositoryProgrammingError("transaction_required")

    @contextmanager
    def _translate_database_errors(self) -> Iterator[None]:
        try:
            yield
        except OperationalError as error:
            code = error.args[0] if error.args else None
            if code in self._RETRYABLE_DATABASE_CODES:
                raise AccountPersistenceError("retryable") from None
            raise AccountPersistenceError("storage_unavailable") from None
        except DatabaseError:
            raise AccountPersistenceError("storage_unavailable") from None
