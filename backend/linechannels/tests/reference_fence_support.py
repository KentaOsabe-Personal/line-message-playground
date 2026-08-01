from uuid import UUID

from linechannels.reference_fence import ReferenceFenceResult


class LockedReferenceFence:
    def lock_existing(self, channel_public_id: UUID) -> ReferenceFenceResult:
        return ReferenceFenceResult("locked")


LOCKED_REFERENCE_FENCE = LockedReferenceFence()
