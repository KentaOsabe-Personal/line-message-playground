from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from django.db import transaction

from linechannels.repositories import LineChannelDirectory

from .gateway import (
    FriendshipSucceeded,
    InvalidLineProof,
    LinePlatformGateway,
    LinePlatformUnavailable,
    VerifyUserTokenSucceeded,
)
from .repositories import AccountRepository, AccountStateError, NewRecipient
from .runtime import LiffLinkedChannelPolicy
from .types import UserAccessToken


@dataclass(frozen=True, slots=True)
class ChannelLinkView:
    channel_id: UUID
    channel_label: str
    channel_state: Literal["active", "inactive"]
    link_state: Literal["unlinked", "linked_enabled", "linked_disabled"]
    friendship_state: Literal["friend", "not_friend", "unknown"]
    delivery_available: bool
    recipient_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RecipientMutationSucceeded:
    recipient: ChannelLinkView


@dataclass(frozen=True, slots=True)
class RecipientMutationFailed:
    code: Literal[
        "channel_not_found",
        "channel_unavailable",
        "provider_mismatch",
        "invalid_line_proof",
        "line_unavailable",
        "recipient_not_found",
    ]


RecipientMutationResult = RecipientMutationSucceeded | RecipientMutationFailed


class DefaultRecipientService:
    def __init__(
        self,
        directory: LineChannelDirectory,
        repository: AccountRepository,
        gateway: LinePlatformGateway | None = None,
        linked_channel_policy: LiffLinkedChannelPolicy | None = None,
        *,
        using: str = "default",
    ) -> None:
        self._directory = directory
        self._repository = repository
        self._gateway = gateway
        self._linked_channel_policy = linked_channel_policy
        self._using = using

    def list_channels(self, identity_id: UUID) -> tuple[ChannelLinkView, ...]:
        identity = self._repository.get_identity(identity_id)
        if identity is None:
            raise AccountStateError("identity_not_found")
        recipients = {
            recipient.channel_id: recipient
            for recipient in self._repository.list_channel_links(identity_id)
        }
        channels = {
            channel.public_id: channel
            for channel in self._directory.list_active_bound()
            if channel.provider_id == identity.provider_id
        }
        for channel_id in recipients:
            channel = self._directory.get(channel_id)
            if channel is not None:
                channels[channel_id] = channel

        return tuple(
            self._project(channels[channel_id], recipients.get(channel_id))
            for channel_id in sorted(channels, key=str)
        )

    def register(
        self,
        identity_id: UUID,
        channel_id: UUID,
        access_token: UserAccessToken | None,
    ) -> RecipientMutationResult:
        if self._gateway is None or self._linked_channel_policy is None:
            return RecipientMutationFailed("line_unavailable")
        identity = self._repository.get_identity(identity_id)
        if identity is None:
            raise AccountStateError("identity_not_found")
        channel = self._directory.get(channel_id)
        failure = self._validate_channel(channel, identity.provider_id)
        if failure is not None:
            return failure

        with transaction.atomic(using=self._using):
            owner = self._repository.lock_owner_account()
            existing = self._repository.get_recipient(
                owner, identity_id, channel_id
            )
            if existing is not None:
                return RecipientMutationSucceeded(
                    self._project(channel, existing)
                )

        friendship_state: Literal["friend", "not_friend", "unknown"] = "unknown"
        if self._linked_channel_policy.is_direct(channel_id):
            if access_token is None:
                return RecipientMutationFailed("invalid_line_proof")
            verification = self._gateway.verify_user_access_token(
                access_token, identity.subject
            )
            if isinstance(verification, InvalidLineProof):
                return RecipientMutationFailed("invalid_line_proof")
            if isinstance(verification, LinePlatformUnavailable):
                return RecipientMutationFailed("line_unavailable")
            if not isinstance(verification, VerifyUserTokenSucceeded):
                return RecipientMutationFailed("line_unavailable")
            friendship = self._gateway.get_friendship(access_token)
            if isinstance(friendship, InvalidLineProof):
                return RecipientMutationFailed("invalid_line_proof")
            if isinstance(friendship, LinePlatformUnavailable):
                return RecipientMutationFailed("line_unavailable")
            if not isinstance(friendship, FriendshipSucceeded):
                return RecipientMutationFailed("line_unavailable")
            friendship_state = "friend" if friendship.is_friend else "not_friend"

        with transaction.atomic(using=self._using):
            owner = self._repository.lock_owner_account()
            current_channel = self._directory.get(channel_id)
            failure = self._validate_channel(
                current_channel, identity.provider_id
            )
            if failure is not None:
                return failure
            recipient = self._repository.create_recipient(
                owner,
                NewRecipient(
                    identity_id=identity_id,
                    channel_id=channel_id,
                    friendship_state=friendship_state,
                ),
            )
        return RecipientMutationSucceeded(
            self._project(current_channel, recipient)
        )

    def set_enabled(
        self,
        identity_id: UUID,
        recipient_id: UUID,
        enabled: bool,
    ) -> RecipientMutationResult:
        identity = self._repository.get_identity(identity_id)
        if identity is None:
            raise AccountStateError("identity_not_found")
        with transaction.atomic(using=self._using):
            owner = self._repository.lock_owner_account()
            existing = self._repository.get_recipient_by_id(
                owner, identity_id, recipient_id
            )
            if existing is None:
                return RecipientMutationFailed("recipient_not_found")
            channel = self._directory.get(existing.channel_id)
            if channel is None:
                return RecipientMutationFailed("channel_unavailable")
            if enabled:
                if not channel.is_active:
                    return RecipientMutationFailed("channel_unavailable")
                if channel.provider_id != identity.provider_id:
                    return RecipientMutationFailed("provider_mismatch")
            recipient = self._repository.set_recipient_enabled(
                owner, identity_id, recipient_id, enabled
            )
        return RecipientMutationSucceeded(self._project(channel, recipient))

    def unlink(
        self, identity_id: UUID, recipient_id: UUID
    ) -> RecipientMutationResult:
        with transaction.atomic(using=self._using):
            owner = self._repository.lock_owner_account()
            existing = self._repository.get_recipient_by_id(
                owner, identity_id, recipient_id
            )
            if existing is None:
                return RecipientMutationFailed("recipient_not_found")
            channel = self._directory.get(existing.channel_id)
            if channel is None:
                return RecipientMutationFailed("channel_unavailable")
            deleted = self._repository.delete_recipient(
                owner, identity_id, recipient_id
            )
            if not deleted:
                return RecipientMutationFailed("recipient_not_found")
        return RecipientMutationSucceeded(self._project(channel, None))

    @staticmethod
    def _validate_channel(channel, provider_id: str) -> RecipientMutationFailed | None:
        if channel is None:
            return RecipientMutationFailed("channel_not_found")
        if not channel.is_active:
            return RecipientMutationFailed("channel_unavailable")
        if channel.provider_id != provider_id:
            return RecipientMutationFailed("provider_mismatch")
        return None

    @staticmethod
    def _project(channel, recipient) -> ChannelLinkView:
        if recipient is None:
            return ChannelLinkView(
                channel_id=channel.public_id,
                channel_label=channel.label,
                channel_state="active" if channel.is_active else "inactive",
                link_state="unlinked",
                friendship_state="unknown",
                delivery_available=False,
            )
        delivery_available = (
            recipient.enabled
            and recipient.friendship_state == "friend"
            and channel.is_active
        )
        return ChannelLinkView(
            channel_id=channel.public_id,
            channel_label=channel.label,
            channel_state="active" if channel.is_active else "inactive",
            link_state=(
                "linked_enabled" if recipient.enabled else "linked_disabled"
            ),
            friendship_state=recipient.friendship_state,
            delivery_available=delivery_available,
            recipient_id=recipient.public_id,
        )
