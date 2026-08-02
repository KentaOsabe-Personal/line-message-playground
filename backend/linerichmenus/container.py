from dataclasses import dataclass

from django.conf import settings

from .services import DefaultMutationReadiness, MutationReadiness


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
