from typing import Protocol, runtime_checkable

from .types import IntegrationNotReady, MutationReady, OperationKind


@runtime_checkable
class MutationReadiness(Protocol):
    def authorize(
        self, kind: OperationKind
    ) -> MutationReady | IntegrationNotReady: ...


class DefaultMutationReadiness:
    _RECOVERY_KINDS = frozenset(
        {
            OperationKind.UNLINK,
            OperationKind.RELEASE,
            OperationKind.RECHECK,
            OperationKind.CLEANUP,
        }
    )

    def __init__(self, *, mode: str, integration_complete: bool) -> None:
        self._mode = mode
        self.configuration_valid = mode in {
            "read_only",
            "recovery_only",
            "enabled",
        } and (mode == "read_only" or integration_complete)

    def authorize(
        self, kind: OperationKind
    ) -> MutationReady | IntegrationNotReady:
        if not isinstance(kind, OperationKind):
            return IntegrationNotReady(reason="unsupported_operation")
        if not self.configuration_valid or self._mode == "read_only":
            return IntegrationNotReady(reason="integration_not_ready")
        if self._mode == "recovery_only" and kind not in self._RECOVERY_KINDS:
            return IntegrationNotReady(reason="integration_not_ready")
        return MutationReady()
