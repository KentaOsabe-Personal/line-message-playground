import logging
import uuid

from cryptography.fernet import Fernet
from django.db import connection
from django.test import TransactionTestCase

from linechannels.crypto import FernetCredentialCipher, parse_credential_keyring
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.repositories import DjangoCredentialRepository, DjangoLineChannelRepository
from linechannels.rotation import DefaultCredentialRotationService
from linechannels.rotation_item import DefaultCredentialRotationItemProcessor
from linechannels.rotation_lock import MySQLRotationLock
from linechannels.rotation_repository import DjangoRotationCredentialRepository
from linechannels.services import DefaultLineChannelService
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialContext,
    EncryptedCredential,
    EncryptedCredentialPair,
    RegisterLineChannel,
    UpdateLineChannel,
)
from linechannels.validators import build_credential_pair


class _RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(self.format(record))


class _InterruptOnSecondItem:
    def __init__(self, delegate):
        self._delegate = delegate
        self._calls = 0

    def process(self, public_id, credentials):
        self._calls += 1
        if self._calls == 2:
            raise KeyboardInterrupt
        return self._delegate.process(public_id, credentials)

    def verify_with_primary(self, public_id, credentials):
        return self._delegate.verify_with_primary(public_id, credentials)


class CredentialIntegrationSecurityTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.primary_key = Fernet.generate_key().decode("ascii")
        self.old_key = Fernet.generate_key().decode("ascii")
        self.primary_cipher = FernetCredentialCipher(
            parse_credential_keyring(self.primary_key)
        )
        self.old_cipher = FernetCredentialCipher(parse_credential_keyring(self.old_key))
        self.rotation_cipher = FernetCredentialCipher(
            parse_credential_keyring(f"{self.primary_key},{self.old_key}")
        )

    def _create_old_credential(self, public_id, marker):
        channel = LineChannel.objects.create(
            public_id=public_id,
            messaging_api_channel_id=str(10**11 + public_id.int % 10**10),
            bot_user_id=f"U{public_id.hex}",
            label="rotation integration",
            is_active=True,
        )
        pair = EncryptedCredentialPair(
            self.old_cipher.encrypt(
                AccessToken(f"{marker}-token"),
                CredentialContext(public_id, "access_token"),
            ),
            self.old_cipher.encrypt(
                ChannelSecret(f"{marker}-secret"),
                CredentialContext(public_id, "channel_secret"),
            ),
        )
        LineChannelCredential.objects.create(
            line_channel=channel,
            access_token_ciphertext=pair.access_token.ciphertext,
            channel_secret_ciphertext=pair.channel_secret.ciphertext,
        )
        return channel, pair

    def _rotation_service(self, processor=None):
        item_processor = processor or DefaultCredentialRotationItemProcessor(
            self.rotation_cipher
        )
        return DefaultCredentialRotationService(
            self.rotation_cipher,
            DjangoRotationCredentialRepository(),
            MySQLRotationLock(),
            item_processor,
        )

    # テストケース: 実暗号と実DBで登録・用途別取得・rotationを実行する
    # 期待値: 平文・暗号文・鍵canaryがquery履歴とDB loggerへ一切現れない
    def test_operations_do_not_expose_credentials_to_queries_or_database_logs(self):
        logger = logging.getLogger("django.db.backends")
        handler = _RecordingHandler()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        public_id = uuid.UUID("00000000-0000-4000-8000-000000000010")
        service = DefaultLineChannelService(
            DjangoLineChannelRepository(),
            self.rotation_cipher,
            uuid_factory=lambda: public_id,
        )
        result = service.register(
            RegisterLineChannel(
                "123456789012",
                "U" + "1" * 32,
                "security integration",
                build_credential_pair(
                    "plaintext-token-canary", "plaintext-secret-canary"
                ),
                True,
            )
        )
        self.assertEqual(result.status, "succeeded")

        repository = DjangoCredentialRepository(self.rotation_cipher)
        self.assertEqual(repository.get_access_token(public_id).status, "available")
        self.assertEqual(repository.get_channel_secret(public_id).status, "available")

        old_id = uuid.UUID("00000000-0000-4000-8000-000000000011")
        _, old_pair = self._create_old_credential(old_id, "rotation-plaintext-canary")
        summary = self._rotation_service().rotate_all()
        self.assertEqual(summary.status, "complete")

        observed = repr(connection.queries) + "\n".join(handler.messages)
        canaries = (
            self.primary_key,
            self.old_key,
            "plaintext-token-canary",
            "plaintext-secret-canary",
            "rotation-plaintext-canary",
            old_pair.access_token.ciphertext.decode("ascii"),
            old_pair.channel_secret.ciphertext.decode("ascii"),
        )
        for canary in canaries:
            with self.subTest(canary_type="secret material"):
                self.assertNotIn(canary, observed)
        self.assertEqual(connection.queries, [])

    # テストケース: 旧鍵資格情報の2行目でrotationを中断して再実行する
    # 期待値: 1行目だけcommitし、再実行で残りだけ現用鍵へ収束する
    def test_interrupted_rotation_preserves_current_row_and_rerun_converges(self):
        first_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
        second_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
        self._create_old_credential(first_id, "first")
        _, second_original = self._create_old_credential(second_id, "second")
        concrete_processor = DefaultCredentialRotationItemProcessor(
            self.rotation_cipher
        )
        interrupting = _InterruptOnSecondItem(concrete_processor)

        with self.assertRaises(KeyboardInterrupt):
            self._rotation_service(interrupting).rotate_all()

        first = LineChannelCredential.objects.get(line_channel__public_id=first_id)
        second = LineChannelCredential.objects.get(line_channel__public_id=second_id)
        first_pair = EncryptedCredentialPair(
            EncryptedCredential(bytes(first.access_token_ciphertext)),
            EncryptedCredential(bytes(first.channel_secret_ciphertext)),
        )
        second_pair = EncryptedCredentialPair(
            EncryptedCredential(bytes(second.access_token_ciphertext)),
            EncryptedCredential(bytes(second.channel_secret_ciphertext)),
        )
        self.assertEqual(
            second_pair.access_token.ciphertext,
            second_original.access_token.ciphertext,
        )
        self.assertEqual(
            second_pair.channel_secret.ciphertext,
            second_original.channel_secret.ciphertext,
        )
        self.assertEqual(
            concrete_processor.verify_with_primary(first_id, first_pair).status,
            "verified",
        )

        rerun = self._rotation_service().rotate_all()

        self.assertEqual(rerun.status, "complete")
        self.assertEqual(rerun.verified_count, 1)
        self.assertEqual(rerun.rotated_count, 1)
        self.assertTrue(rerun.old_keys_removable)

    # テストケース: 旧鍵行と破損行を含むDBをrotation後に修復して再実行する
    # 期待値: 破損暗号文を保持してincompleteとなり、修復後だけcompleteになる
    def test_corrupt_row_is_preserved_until_repaired_then_rerun_completes(self):
        healthy_id = uuid.UUID("00000000-0000-4000-8000-000000000041")
        corrupt_id = uuid.UUID("00000000-0000-4000-8000-000000000042")
        self._create_old_credential(healthy_id, "healthy")
        self._create_old_credential(corrupt_id, "corrupt")
        corrupt_access = b"corrupt-access-ciphertext"
        corrupt_secret = b"corrupt-secret-ciphertext"
        LineChannelCredential.objects.filter(
            line_channel__public_id=corrupt_id
        ).update(
            access_token_ciphertext=corrupt_access,
            channel_secret_ciphertext=corrupt_secret,
        )

        first_run = self._rotation_service().rotate_all()

        corrupt = LineChannelCredential.objects.get(
            line_channel__public_id=corrupt_id
        )
        self.assertEqual(first_run.status, "incomplete")
        self.assertFalse(first_run.old_keys_removable)
        self.assertEqual(first_run.failed_count, 1)
        self.assertEqual(first_run.failures[0].channel_public_id, corrupt_id)
        self.assertEqual(first_run.failures[0].code, "credential_unreadable")
        self.assertEqual(bytes(corrupt.access_token_ciphertext), corrupt_access)
        self.assertEqual(bytes(corrupt.channel_secret_ciphertext), corrupt_secret)

        repair_service = DefaultLineChannelService(
            DjangoLineChannelRepository(), self.rotation_cipher
        )
        repaired = repair_service.update(
            UpdateLineChannel(
                corrupt_id,
                credentials=build_credential_pair(
                    "repaired-token", "repaired-secret"
                ),
            )
        )
        second_run = self._rotation_service().rotate_all()

        self.assertEqual(repaired.status, "succeeded")
        self.assertEqual(second_run.status, "complete")
        self.assertEqual(second_run.verified_count, 2)
        self.assertEqual(second_run.rotated_count, 0)
        self.assertTrue(second_run.old_keys_removable)
