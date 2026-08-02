from dataclasses import dataclass

from django.conf import settings

from lineaccounts.admin_authorization import DjangoOwnerOperationFence
from lineaccounts.repositories import DjangoAccountRepository
from linechannels import runtime as channel_runtime
from linechannels.admin_repositories import DjangoAdminChannelRepository
from linechannels.admin_types import ChannelRevisionProof, ChannelRevisionUnchanged
from linechannels.crypto import FernetCredentialCipher

from .catalog import DefaultTemplateCatalog
from .confirmation import DefaultRichMenuConfirmation
from .gateway import DefaultRichMenuGateway
from .headless import DefaultRichMenuLifecyclePort, DjangoHeadlessReferenceContracts
from .reconciliation import DefaultRichMenuReconciler
from .renderer import DefaultDeterministicRenderer
from .repository import (
    DjangoRichMenuRepository,
    OperationFenceResult,
    OperationFenceSnapshot,
)
from .services import DefaultMutationReadiness, DefaultRichMenuService, MutationReadiness


LIFECYCLE_INTEGRATION_MARKER = "line-rich-menu-admin-lifecycle-v1"


@dataclass(frozen=True, slots=True)
class ReadinessConfigurationFailure:
    code: str = "integration_not_ready"
    message: str = "リッチメニュー変更の統合準備を確認できません。"


def build_mutation_readiness(
    *,
    mode: str,
    reference_probe_integrated: bool = False,
    history_purge_integrated: bool = False,
    integration_marker: str = "",
) -> DefaultMutationReadiness:
    integration_complete = (
        reference_probe_integrated is True
        and history_purge_integrated is True
        and integration_marker == LIFECYCLE_INTEGRATION_MARKER
    )
    return DefaultMutationReadiness(
        mode=mode,
        integration_complete=integration_complete,
    )


def build_configured_mutation_readiness() -> MutationReadiness:
    return build_mutation_readiness(
        mode=settings.LINE_RICH_MENU_MUTATION_MODE,
        reference_probe_integrated=settings.LINE_RICH_MENU_REFERENCE_PROBE_INTEGRATED,
        history_purge_integrated=settings.LINE_RICH_MENU_HISTORY_PURGE_INTEGRATED,
        integration_marker=settings.LINE_RICH_MENU_INTEGRATION_MARKER,
    )


def validate_mutation_readiness_configuration(
) -> tuple[ReadinessConfigurationFailure, ...]:
    readiness = build_configured_mutation_readiness()
    if readiness.configuration_valid:
        return ()
    return (ReadinessConfigurationFailure(),)


class _ChannelOperationFence:
    def __init__(self, channel_port) -> None:
        self._channel_port = channel_port

    def lock_exact(self, snapshot: OperationFenceSnapshot) -> OperationFenceResult:
        try:
            result = self._channel_port.lock_unchanged(
                ChannelRevisionProof(
                    owner_identity_public_id=snapshot.owner_identity_public_id,
                    provider_id=snapshot.provider_id,
                    channel_public_id=snapshot.channel_public_id,
                    channel_revision=snapshot.expected_channel_revision,
                )
            )
        except Exception:
            return OperationFenceResult("unavailable")
        if isinstance(result, ChannelRevisionUnchanged):
            return OperationFenceResult("matched")
        return OperationFenceResult(
            "stale" if getattr(result, "code", None) == "stale_channel" else "unavailable"
        )


def build_rich_menu_service() -> DefaultRichMenuService:
    cipher = FernetCredentialCipher(channel_runtime.get_validated_keyring())
    channel_port = DjangoAdminChannelRepository(cipher)
    gateway = DefaultRichMenuGateway()
    catalog = DefaultTemplateCatalog()
    repository = DjangoRichMenuRepository(
        operation_fence=_ChannelOperationFence(channel_port)
    )
    return DefaultRichMenuService(
        owner_fence=DjangoOwnerOperationFence(DjangoAccountRepository()),
        channel_port=channel_port,
        repository=repository,
        gateway=gateway,
        reconciler=DefaultRichMenuReconciler(gateway),
        catalog=catalog,
        renderer=DefaultDeterministicRenderer(catalog=catalog),
        confirmation=DefaultRichMenuConfirmation(),
        readiness=build_configured_mutation_readiness(),
    )


def build_headless_lifecycle_port() -> DefaultRichMenuLifecyclePort:
    return DefaultRichMenuLifecyclePort(build_rich_menu_service())


def build_headless_reference_contracts() -> DjangoHeadlessReferenceContracts:
    return DjangoHeadlessReferenceContracts()
