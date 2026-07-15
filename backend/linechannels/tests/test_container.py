from unittest.mock import patch

from django.test import TestCase

from linechannels import container
from linechannels.crypto import FernetCredentialCipher
from linechannels.management.prompts import GetPassManageLineChannelPrompts
from linechannels.repositories import (
    CredentialRepository,
    DjangoCredentialRepository,
    DjangoLineChannelRepository,
)
from linechannels.rotation import DefaultCredentialRotationService
from linechannels.rotation_item import DefaultCredentialRotationItemProcessor
from linechannels.rotation_lock import MySQLRotationLock
from linechannels.rotation_repository import DjangoRotationCredentialRepository
from linechannels.services import DefaultLineChannelService


class CompositionRootTests(TestCase):
    # テストケース: 検証済みkeyringから用途別資格情報repositoryを構築する
    # 期待値: 公開Protocolへ適合する具象repositoryだけが返される
    def test_builds_purpose_specific_credential_repository(self):
        repository = container.build_credential_repository()

        self.assertIsInstance(repository, DjangoCredentialRepository)
        self.assertIsInstance(repository, CredentialRepository)
        self.assertIsInstance(repository._cipher, FernetCredentialCipher)

    # テストケース: チャネル管理serviceをcomposition rootから構築する
    # 期待値: 通常repositoryとcipherをconstructor injectionしたserviceが返される
    def test_builds_line_channel_service_with_concrete_dependencies(self):
        service = container.build_line_channel_service()

        self.assertIsInstance(service, DefaultLineChannelService)
        self.assertIsInstance(service._repository, DjangoLineChannelRepository)
        self.assertIsInstance(service._cipher, FernetCredentialCipher)

    # テストケース: ローテーションserviceをcomposition rootから構築する
    # 期待値: serviceと1行processorが同一cipher instanceを共有する
    def test_builds_rotation_service_with_one_shared_cipher(self):
        service = container.build_rotation_service()

        self.assertIsInstance(service, DefaultCredentialRotationService)
        self.assertIsInstance(service._repository, DjangoRotationCredentialRepository)
        self.assertIsInstance(service._rotation_lock, MySQLRotationLock)
        self.assertIsInstance(
            service._item_processor, DefaultCredentialRotationItemProcessor
        )
        self.assertIs(service._cipher, service._item_processor._cipher)

    # テストケース: 対話prompt factoryを呼び出す
    # 期待値: keyringやDBへ触れずTTY安全なpromptだけを構築する
    def test_builds_prompts_without_reading_runtime_keyring(self):
        with patch.object(
            container.runtime,
            "get_validated_keyring",
            side_effect=AssertionError("runtime keyring must not be read"),
        ):
            prompts = container.build_manage_line_channel_prompts()

        self.assertIsInstance(prompts, GetPassManageLineChannelPrompts)

    # テストケース: 各factoryをDB queryなしで構築する
    # 期待値: composition rootは組立てだけを行いreadinessや永続化へ進まない
    def test_factories_construct_dependencies_without_database_queries(self):
        with self.assertNumQueries(0):
            container.build_credential_repository()
            container.build_line_channel_service()
            container.build_rotation_service()
            container.build_manage_line_channel_prompts()
