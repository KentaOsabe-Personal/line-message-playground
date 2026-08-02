from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from django.apps import AppConfig
from django.core.checks import Error, register


EXPECTED_PILLOW_VERSION = "12.3.0"
EXPECTED_FONT_VERSION = "2.004"
EXPECTED_FONT_DIGEST = "dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073"
EXPECTED_LICENSE_DIGEST = "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"
FONT_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_PATH = FONT_ASSET_DIR / "NotoSansJP-Regular.otf"
_CHECK_IDS = {
    "pillow_version": "linerichmenus.E001",
    "font_asset": "linerichmenus.E002",
    "font_version": "linerichmenus.E003",
    "font_digest": "linerichmenus.E004",
    "font_license": "linerichmenus.E005",
    "runtime_validation": "linerichmenus.E006",
    "integration_not_ready": "linerichmenus.E010",
}


@dataclass(frozen=True, slots=True)
class RuntimePrerequisiteFailure:
    code: str
    message: str


def validate_runtime_prerequisites(
    *,
    asset_dir: Path | None = None,
    pillow_version: str | None = None,
) -> tuple[RuntimePrerequisiteFailure, ...]:
    if asset_dir is None:
        asset_dir = FONT_ASSET_DIR
    if pillow_version is None:
        from PIL import __version__ as pillow_version

    failures: list[RuntimePrerequisiteFailure] = []
    if pillow_version != EXPECTED_PILLOW_VERSION:
        failures.append(
            RuntimePrerequisiteFailure(
                code="pillow_version",
                message="固定された画像処理ランタイムを確認できません。",
            )
        )

    font_bytes = _read_asset(asset_dir / "NotoSansJP-Regular.otf")
    if font_bytes is None:
        failures.append(
            RuntimePrerequisiteFailure(
                code="font_asset",
                message="固定されたフォント資産を確認できません。",
            )
        )
    else:
        version_marker = f"Version {EXPECTED_FONT_VERSION}".encode("utf-16-be")
        if version_marker not in font_bytes:
            failures.append(
                RuntimePrerequisiteFailure(
                    code="font_version",
                    message="固定されたフォント版を確認できません。",
                )
            )
        if sha256(font_bytes).hexdigest() != EXPECTED_FONT_DIGEST:
            failures.append(
                RuntimePrerequisiteFailure(
                    code="font_digest",
                    message="固定されたフォント資産の完全性を確認できません。",
                )
            )

    license_bytes = _read_asset(asset_dir / "OFL-1.1.txt")
    if (
        license_bytes is None
        or sha256(license_bytes).hexdigest() != EXPECTED_LICENSE_DIGEST
    ):
        failures.append(
            RuntimePrerequisiteFailure(
                code="font_license",
                message="フォントライセンス資産を確認できません。",
            )
        )

    return tuple(failures)


def runtime_prerequisites_available() -> bool:
    try:
        return not validate_runtime_prerequisites()
    except Exception:
        return False


@register()
def check_runtime_prerequisites(app_configs=None, **kwargs):
    try:
        failures = validate_runtime_prerequisites()
    except Exception:
        failures = (
            RuntimePrerequisiteFailure(
                code="runtime_validation",
                message="画像生成ランタイムを安全に検証できません。",
            ),
        )
    return [
        Error(
            failure.message,
            hint="Backend image build and bundled assets must match the approved versions.",
            id=_CHECK_IDS[failure.code],
        )
        for failure in failures
    ]


@register()
def check_mutation_readiness(app_configs=None, **kwargs):
    from .container import validate_mutation_readiness_configuration

    return [
        Error(
            failure.message,
            hint="Keep read_only, or integrate reference probe, rollback-only purge, and the approved marker together.",
            id=_CHECK_IDS[failure.code],
        )
        for failure in validate_mutation_readiness_configuration()
    ]


def _read_asset(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except (OSError, ValueError):
        return None


class LineRichMenusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "linerichmenus"
