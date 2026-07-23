from uuid import UUID

from lineaccounts.types import LineSubject
from lineinteractions.types import (
    LinkedInteractionUserMissing,
    VerifiedInteractionUser,
)

from .models import DeliveryRecipient, OwnerAccount


class DjangoInteractionAccountDirectory:
    def __init__(self, using: str = "default") -> None:
        self.using = using

    def resolve_linked(
        self,
        *,
        channel_public_id: UUID,
        provider_id: str,
        subject: LineSubject,
    ) -> VerifiedInteractionUser | LinkedInteractionUserMissing:
        if (
            not isinstance(provider_id, str)
            or not isinstance(subject, LineSubject)
        ):
            return LinkedInteractionUserMissing()
        row = (
            DeliveryRecipient.objects.using(self.using)
            .filter(
                line_channel__public_id=channel_public_id,
                line_channel__provider_id=provider_id,
                identity__provider_id=provider_id,
                identity__subject=subject.reveal_for_identity_binding(),
                identity__owner_account__state=OwnerAccount.State.ACTIVE,
            )
            .values("identity__public_id", "public_id")
            .first()
        )
        if row is None:
            return LinkedInteractionUserMissing()
        return VerifiedInteractionUser(
            identity_public_id=row["identity__public_id"],
            recipient_public_id=row["public_id"],
        )
