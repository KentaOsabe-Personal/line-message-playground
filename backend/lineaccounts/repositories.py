"""LINE account aggregate の Django 永続化境界。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Literal, Protocol, runtime_checkable
from uuid import UUID

from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.utils import timezone

from linechannels.models import LineChannel

from .gateway import VerifiedLineIdentity
from .models import DeliveryRecipient, LineIdentity, OwnerAccount, OwnerSession
from .types import LineSubject


PersistenceFailureCode = Literal[
    "unique_conflict",
    "retryable",
    "storage_unavailable",
]
ProgrammingErrorCode = Literal["transaction_required", "invalid_command"]
AccountStateErrorCode = Literal[
    "owner_not_active",
    "identity_mismatch",
    "identity_not_found",
    "channel_not_found",
    "recipient_not_found",
    "invalid_unlink_stage",
    "unlink_attempt_stale",
]


class AccountPersistenceError(RuntimeError):
    def __init__(self, code: PersistenceFailureCode) -> None:
        self.code = code
        super().__init__(code)


class AccountRepositoryProgrammingError(RuntimeError):
    def __init__(self, code: ProgrammingErrorCode) -> None:
        self.code = code
        super().__init__(code)


class AccountStateError(RuntimeError):
    def __init__(self, code: AccountStateErrorCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LineIdentityView:
    public_id: UUID
    provider_id: str
    subject: LineSubject
    display_name: str


@dataclass(frozen=True, slots=True)
class LockedOwnerAccount:
    slot: int
    state: str
    identity_id: UUID | None
    unlink_generation: UUID | None
    line_deauthorized_at: datetime | None


@dataclass(frozen=True, slots=True)
class OwnerSessionView:
    public_id: UUID
    owner_slot: int
    identity_id: UUID
    display_name: str
    owner_state: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RecipientView:
    public_id: UUID
    identity_id: UUID
    channel_id: UUID
    enabled: bool
    friendship_state: str


@dataclass(frozen=True, slots=True)
class NewRecipient:
    identity_id: UUID
    channel_id: UUID
    friendship_state: Literal["friend", "not_friend", "unknown"]


@dataclass(frozen=True, slots=True)
class UnlinkSnapshot:
    owner_slot: int
    identity_id: UUID
    display_name: str
    recipient_ids: tuple[UUID, ...]
    channel_ids: tuple[UUID, ...]


@runtime_checkable
class AccountRepository(Protocol):
    def get_identity(self, public_id: UUID) -> LineIdentityView | None: ...

    def lock_owner_account(self) -> LockedOwnerAccount: ...

    def upsert_identity(self, identity: VerifiedLineIdentity) -> LineIdentityView: ...

    def bind_owner_identity(
        self, owner: LockedOwnerAccount, identity_id: UUID
    ) -> LockedOwnerAccount: ...

    def create_owner_session(
        self, owner: LockedOwnerAccount, expires_at: datetime
    ) -> OwnerSessionView: ...

    def get_session(
        self, public_id: UUID, now: datetime
    ) -> OwnerSessionView | None: ...

    def delete_owner_session(self, public_id: UUID) -> bool: ...

    def list_channel_links(self, identity_id: UUID) -> tuple[RecipientView, ...]: ...

    def create_recipient(
        self, owner: LockedOwnerAccount, command: NewRecipient
    ) -> RecipientView: ...

    def get_recipient(
        self,
        owner: LockedOwnerAccount,
        identity_id: UUID,
        channel_id: UUID,
    ) -> RecipientView | None: ...

    def get_recipient_by_id(
        self,
        owner: LockedOwnerAccount,
        identity_id: UUID,
        recipient_id: UUID,
    ) -> RecipientView | None: ...

    def set_recipient_enabled(
        self,
        owner: LockedOwnerAccount,
        identity_id: UUID,
        recipient_id: UUID,
        enabled: bool,
    ) -> RecipientView: ...

    def delete_recipient(
        self, owner: LockedOwnerAccount, identity_id: UUID, recipient_id: UUID
    ) -> bool: ...

    def get_unlink_snapshot(self, owner: LockedOwnerAccount) -> UnlinkSnapshot: ...

    def begin_unlink(
        self, owner: LockedOwnerAccount, generation: UUID
    ) -> LockedOwnerAccount: ...

    def mark_line_deauthorized(
        self,
        owner: LockedOwnerAccount,
        expected_generation: UUID,
        confirmed_at: datetime,
    ) -> LockedOwnerAccount: ...

    def finalize_unlink(
        self, owner: LockedOwnerAccount, expected_generation: UUID
    ) -> None: ...


class DjangoAccountRepository:
    """OwnerAccount singleton を全 mutation の線形化点にする adapter。"""

    _RETRYABLE_DATABASE_CODES = frozenset((1205, 1213))

    def __init__(self, using: str = "default") -> None:
        self.using = using

    def get_identity(self, public_id: UUID) -> LineIdentityView | None:
        with self._translate_database_errors():
            identity = (
                LineIdentity.objects.using(self.using)
                .filter(public_id=public_id)
                .first()
            )
            return None if identity is None else self._identity_view(identity)

    def lock_owner_account(self) -> LockedOwnerAccount:
        self._require_transaction()
        with self._translate_database_errors():
            owner = (
                OwnerAccount.objects.using(self.using)
                .select_for_update()
                .select_related("identity")
                .get(slot=1)
            )
            return self._owner_view(owner)

    def upsert_identity(self, identity: VerifiedLineIdentity) -> LineIdentityView:
        self._require_transaction()
        subject = identity.subject.reveal_for_identity_binding()
        with self._translate_database_errors():
            stored = (
                LineIdentity.objects.using(self.using)
                .select_for_update()
                .filter(provider_id=identity.provider_id, subject=subject)
                .first()
            )
            if stored is None:
                try:
                    with transaction.atomic(using=self.using):
                        stored = LineIdentity.objects.using(self.using).create(
                            provider_id=identity.provider_id,
                            subject=subject,
                            display_name=identity.display_name,
                        )
                except IntegrityError:
                    stored = (
                        LineIdentity.objects.using(self.using)
                        .select_for_update()
                        .get(provider_id=identity.provider_id, subject=subject)
                    )
            elif stored.display_name != identity.display_name:
                stored.display_name = identity.display_name
                stored.updated_at = timezone.now()
                stored.save(
                    using=self.using,
                    update_fields=("display_name", "updated_at"),
                )
            return self._identity_view(stored)

    def bind_owner_identity(
        self, owner: LockedOwnerAccount, identity_id: UUID
    ) -> LockedOwnerAccount:
        self._require_transaction()
        with self._translate_database_errors():
            stored_owner = self._locked_owner(owner)
            identity = (
                LineIdentity.objects.using(self.using)
                .filter(public_id=identity_id)
                .first()
            )
            if identity is None:
                raise AccountStateError("identity_not_found")

            if stored_owner.state == OwnerAccount.State.VACANT:
                stored_owner.identity = identity
                stored_owner.state = OwnerAccount.State.ACTIVE
                stored_owner.updated_at = timezone.now()
                stored_owner.save(
                    using=self.using,
                    update_fields=("identity", "state", "updated_at"),
                )
            elif stored_owner.identity_id != identity.pk:
                raise AccountStateError("identity_mismatch")
            return self._owner_view(stored_owner)

    def create_owner_session(
        self, owner: LockedOwnerAccount, expires_at: datetime
    ) -> OwnerSessionView:
        self._require_transaction()
        if timezone.is_naive(expires_at):
            raise AccountRepositoryProgrammingError("invalid_command")
        with self._translate_database_errors():
            stored_owner = self._locked_owner(owner)
            if stored_owner.identity is None:
                raise AccountStateError("identity_not_found")
            session = OwnerSession.objects.using(self.using).create(
                owner=stored_owner,
                expires_at=expires_at,
            )
            return self._session_view(session, stored_owner)

    def get_session(
        self, public_id: UUID, now: datetime
    ) -> OwnerSessionView | None:
        if timezone.is_naive(now):
            raise AccountRepositoryProgrammingError("invalid_command")
        with self._translate_database_errors():
            session = (
                OwnerSession.objects.using(self.using)
                .select_related("owner__identity")
                .filter(public_id=public_id)
                .first()
            )
            if session is None:
                return None
            if session.expires_at <= now or session.owner.identity is None:
                OwnerSession.objects.using(self.using).filter(pk=session.pk).delete()
                return None
            return self._session_view(session, session.owner)

    def delete_owner_session(self, public_id: UUID) -> bool:
        self._require_transaction()
        with self._translate_database_errors():
            deleted, _ = (
                OwnerSession.objects.using(self.using)
                .filter(public_id=public_id)
                .delete()
            )
            return deleted == 1

    def list_channel_links(self, identity_id: UUID) -> tuple[RecipientView, ...]:
        with self._translate_database_errors():
            recipients = (
                DeliveryRecipient.objects.using(self.using)
                .select_related("identity", "line_channel")
                .filter(identity__public_id=identity_id)
                .order_by("public_id")
            )
            return tuple(self._recipient_view(recipient) for recipient in recipients)

    def create_recipient(
        self, owner: LockedOwnerAccount, command: NewRecipient
    ) -> RecipientView:
        self._require_transaction()
        self._validate_friendship_state(command.friendship_state)
        with self._translate_database_errors():
            stored_owner = self._active_owner_for_identity(owner, command.identity_id)
            channel = (
                LineChannel.objects.using(self.using)
                .filter(public_id=command.channel_id)
                .first()
            )
            if channel is None:
                raise AccountStateError("channel_not_found")
            recipient = (
                DeliveryRecipient.objects.using(self.using)
                .select_related("identity", "line_channel")
                .filter(identity=stored_owner.identity, line_channel=channel)
                .first()
            )
            if recipient is None:
                try:
                    with transaction.atomic(using=self.using):
                        recipient = DeliveryRecipient.objects.using(self.using).create(
                            identity=stored_owner.identity,
                            line_channel=channel,
                            friendship_state=command.friendship_state,
                        )
                except IntegrityError:
                    recipient = (
                        DeliveryRecipient.objects.using(self.using)
                        .select_related("identity", "line_channel")
                        .get(identity=stored_owner.identity, line_channel=channel)
                    )
            return self._recipient_view(recipient)

    def get_recipient(
        self,
        owner: LockedOwnerAccount,
        identity_id: UUID,
        channel_id: UUID,
    ) -> RecipientView | None:
        self._require_transaction()
        with self._translate_database_errors():
            stored_owner = self._active_owner_for_identity(owner, identity_id)
            recipient = (
                DeliveryRecipient.objects.using(self.using)
                .select_for_update()
                .select_related("identity", "line_channel")
                .filter(
                    identity=stored_owner.identity,
                    line_channel__public_id=channel_id,
                )
                .first()
            )
            return None if recipient is None else self._recipient_view(recipient)

    def get_recipient_by_id(
        self,
        owner: LockedOwnerAccount,
        identity_id: UUID,
        recipient_id: UUID,
    ) -> RecipientView | None:
        self._require_transaction()
        with self._translate_database_errors():
            stored_owner = self._active_owner_for_identity(owner, identity_id)
            recipient = (
                DeliveryRecipient.objects.using(self.using)
                .select_for_update()
                .select_related("identity", "line_channel")
                .filter(
                    identity=stored_owner.identity,
                    public_id=recipient_id,
                )
                .first()
            )
            return None if recipient is None else self._recipient_view(recipient)

    def set_recipient_enabled(
        self,
        owner: LockedOwnerAccount,
        identity_id: UUID,
        recipient_id: UUID,
        enabled: bool,
    ) -> RecipientView:
        self._require_transaction()
        if type(enabled) is not bool:
            raise AccountRepositoryProgrammingError("invalid_command")
        with self._translate_database_errors():
            stored_owner = self._active_owner_for_identity(owner, identity_id)
            recipient = (
                DeliveryRecipient.objects.using(self.using)
                .select_for_update()
                .select_related("identity", "line_channel")
                .filter(
                    public_id=recipient_id,
                    identity=stored_owner.identity,
                )
                .first()
            )
            if recipient is None:
                raise AccountStateError("recipient_not_found")
            if recipient.enabled != enabled:
                recipient.enabled = enabled
                recipient.updated_at = timezone.now()
                recipient.save(
                    using=self.using,
                    update_fields=("enabled", "updated_at"),
                )
            return self._recipient_view(recipient)

    def delete_recipient(
        self, owner: LockedOwnerAccount, identity_id: UUID, recipient_id: UUID
    ) -> bool:
        self._require_transaction()
        with self._translate_database_errors():
            stored_owner = self._active_owner_for_identity(owner, identity_id)
            deleted, _ = (
                DeliveryRecipient.objects.using(self.using)
                .filter(
                    public_id=recipient_id,
                    identity=stored_owner.identity,
                )
                .delete()
            )
            return deleted == 1

    def get_unlink_snapshot(self, owner: LockedOwnerAccount) -> UnlinkSnapshot:
        self._require_transaction()
        with self._translate_database_errors():
            stored_owner = self._locked_owner(owner)
            if (
                stored_owner.state != OwnerAccount.State.ACTIVE
                or stored_owner.identity is None
            ):
                raise AccountStateError("owner_not_active")
            recipients = tuple(
                DeliveryRecipient.objects.using(self.using)
                .filter(identity=stored_owner.identity)
                .values_list("public_id", "line_channel__public_id")
            )
            return UnlinkSnapshot(
                owner_slot=stored_owner.slot,
                identity_id=stored_owner.identity.public_id,
                display_name=stored_owner.identity.display_name,
                recipient_ids=tuple(sorted((row[0] for row in recipients), key=str)),
                channel_ids=tuple(sorted((row[1] for row in recipients), key=str)),
            )

    def begin_unlink(
        self, owner: LockedOwnerAccount, generation: UUID
    ) -> LockedOwnerAccount:
        self._require_transaction()
        if not isinstance(generation, UUID):
            raise AccountRepositoryProgrammingError("invalid_command")
        with self._translate_database_errors():
            stored_owner = self._locked_owner(owner)
            if (
                stored_owner.state != OwnerAccount.State.ACTIVE
                or stored_owner.identity is None
            ):
                raise AccountStateError("invalid_unlink_stage")
            stored_owner.state = OwnerAccount.State.DEAUTHORIZATION_PENDING
            stored_owner.unlink_generation = generation
            stored_owner.line_deauthorized_at = None
            stored_owner.updated_at = timezone.now()
            stored_owner.save(
                using=self.using,
                update_fields=(
                    "state",
                    "unlink_generation",
                    "line_deauthorized_at",
                    "updated_at",
                ),
            )
            return self._owner_view(stored_owner)

    def mark_line_deauthorized(
        self,
        owner: LockedOwnerAccount,
        expected_generation: UUID,
        confirmed_at: datetime,
    ) -> LockedOwnerAccount:
        self._require_transaction()
        if timezone.is_naive(confirmed_at):
            raise AccountRepositoryProgrammingError("invalid_command")
        with self._translate_database_errors():
            stored_owner = self._locked_owner(owner)
            self._require_generation(stored_owner, expected_generation)
            if stored_owner.state == OwnerAccount.State.LOCAL_DELETION_PENDING:
                return self._owner_view(stored_owner)
            if stored_owner.state != OwnerAccount.State.DEAUTHORIZATION_PENDING:
                raise AccountStateError("invalid_unlink_stage")
            stored_owner.state = OwnerAccount.State.LOCAL_DELETION_PENDING
            stored_owner.line_deauthorized_at = confirmed_at
            stored_owner.updated_at = timezone.now()
            stored_owner.save(
                using=self.using,
                update_fields=("state", "line_deauthorized_at", "updated_at"),
            )
            return self._owner_view(stored_owner)

    def finalize_unlink(
        self, owner: LockedOwnerAccount, expected_generation: UUID
    ) -> None:
        self._require_transaction()
        with self._translate_database_errors():
            stored_owner = self._locked_owner(owner)
            self._require_generation(stored_owner, expected_generation)
            if (
                stored_owner.state != OwnerAccount.State.LOCAL_DELETION_PENDING
                or stored_owner.line_deauthorized_at is None
                or stored_owner.identity is None
            ):
                raise AccountStateError("invalid_unlink_stage")

            identity = stored_owner.identity
            DeliveryRecipient.objects.using(self.using).filter(identity=identity).delete()
            self._delete_owner_sessions(stored_owner)
            stored_owner.identity = None
            stored_owner.state = OwnerAccount.State.VACANT
            stored_owner.unlink_generation = None
            stored_owner.line_deauthorized_at = None
            stored_owner.updated_at = timezone.now()
            stored_owner.save(
                using=self.using,
                update_fields=(
                    "identity",
                    "state",
                    "unlink_generation",
                    "line_deauthorized_at",
                    "updated_at",
                ),
            )
            identity.delete(using=self.using)

    def _delete_owner_sessions(self, owner: OwnerAccount) -> None:
        OwnerSession.objects.using(self.using).filter(owner=owner).delete()

    def _active_owner_for_identity(
        self, owner: LockedOwnerAccount, identity_id: UUID
    ) -> OwnerAccount:
        stored_owner = self._locked_owner(owner)
        if stored_owner.state != OwnerAccount.State.ACTIVE:
            raise AccountStateError("owner_not_active")
        if (
            stored_owner.identity is None
            or stored_owner.identity.public_id != identity_id
        ):
            raise AccountStateError("identity_mismatch")
        return stored_owner

    def _locked_owner(self, owner: LockedOwnerAccount) -> OwnerAccount:
        if owner.slot != 1:
            raise AccountRepositoryProgrammingError("invalid_command")
        return (
            OwnerAccount.objects.using(self.using)
            .select_for_update()
            .select_related("identity")
            .get(slot=owner.slot)
        )

    @staticmethod
    def _require_generation(owner: OwnerAccount, expected_generation: UUID) -> None:
        if owner.unlink_generation != expected_generation:
            raise AccountStateError("unlink_attempt_stale")

    @staticmethod
    def _validate_friendship_state(value: str) -> None:
        if value not in DeliveryRecipient.FriendshipState.values:
            raise AccountRepositoryProgrammingError("invalid_command")

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

    @staticmethod
    def _identity_view(identity: LineIdentity) -> LineIdentityView:
        return LineIdentityView(
            public_id=identity.public_id,
            provider_id=identity.provider_id,
            subject=LineSubject(identity.subject),
            display_name=identity.display_name,
        )

    @staticmethod
    def _owner_view(owner: OwnerAccount) -> LockedOwnerAccount:
        return LockedOwnerAccount(
            slot=owner.slot,
            state=owner.state,
            identity_id=None if owner.identity is None else owner.identity.public_id,
            unlink_generation=owner.unlink_generation,
            line_deauthorized_at=owner.line_deauthorized_at,
        )

    @staticmethod
    def _session_view(
        session: OwnerSession, owner: OwnerAccount
    ) -> OwnerSessionView:
        assert owner.identity is not None
        return OwnerSessionView(
            public_id=session.public_id,
            owner_slot=owner.slot,
            identity_id=owner.identity.public_id,
            display_name=owner.identity.display_name,
            owner_state=owner.state,
            expires_at=session.expires_at,
        )

    @staticmethod
    def _recipient_view(recipient: DeliveryRecipient) -> RecipientView:
        return RecipientView(
            public_id=recipient.public_id,
            identity_id=recipient.identity.public_id,
            channel_id=recipient.line_channel.public_id,
            enabled=recipient.enabled,
            friendship_state=recipient.friendship_state,
        )
