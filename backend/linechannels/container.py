"""Validated runtime stateからLINEチャネル基盤を構築するcomposition root。"""

from . import runtime
from .crypto import FernetCredentialCipher
from .management.prompts import GetPassManageLineChannelPrompts, ManageLineChannelPrompts
from .repositories import (
    CredentialRepository,
    DjangoCredentialRepository,
    DjangoLineChannelRepository,
)
from .rotation import CredentialRotationService, DefaultCredentialRotationService
from .rotation_item import DefaultCredentialRotationItemProcessor
from .rotation_lock import MySQLRotationLock
from .rotation_repository import DjangoRotationCredentialRepository
from .services import DefaultLineChannelService, LineChannelService


def _build_cipher() -> FernetCredentialCipher:
    return FernetCredentialCipher(runtime.get_validated_keyring())


def build_credential_repository() -> CredentialRepository:
    return DjangoCredentialRepository(_build_cipher())


def build_line_channel_service() -> LineChannelService:
    cipher = _build_cipher()
    return DefaultLineChannelService(DjangoLineChannelRepository(), cipher)


def build_rotation_service() -> CredentialRotationService:
    cipher = _build_cipher()
    item_processor = DefaultCredentialRotationItemProcessor(cipher)
    return DefaultCredentialRotationService(
        cipher,
        DjangoRotationCredentialRepository(),
        MySQLRotationLock(),
        item_processor,
    )


def build_manage_line_channel_prompts() -> ManageLineChannelPrompts:
    return GetPassManageLineChannelPrompts()
