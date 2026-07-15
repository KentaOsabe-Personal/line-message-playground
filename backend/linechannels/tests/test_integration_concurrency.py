import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from cryptography.fernet import Fernet
from django.db import close_old_connections
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
    EncryptedCredentialPair,
    RegisterLineChannel,
    UpdateLineChannel,
)
from linechannels.validators import build_credential_pair


def _run_with_independent_connection(function):
    close_old_connections()
    try:
        return function()
    finally:
        close_old_connections()


class _BlockingLineChannelRepository(DjangoLineChannelRepository):
    def __init__(self, locked, release):
        super().__init__()
        self._locked = locked
        self._release = release

    def get_for_update(self, public_id):
        value = super().get_for_update(public_id)
        self._locked.set()
        if not self._release.wait(timeout=5):
            raise RuntimeError("concurrency test timed out")
        return value


class _CoordinatedRotationRepository(DjangoRotationCredentialRepository):
    def __init__(self, rotation_locked, release_rotation, update_done):
        super().__init__()
        self._rotation_locked = rotation_locked
        self._release_rotation = release_rotation
        self._update_done = update_done
        self._list_calls = 0
        self._get_calls = 0

    def list_credential_public_ids(self):
        self._list_calls += 1
        if self._list_calls == 2 and not self._update_done.wait(timeout=5):
            raise RuntimeError("concurrent update did not finish")
        return super().list_credential_public_ids()

    def get_credentials_for_update(self, public_id):
        value = super().get_credentials_for_update(public_id)
        self._get_calls += 1
        if self._get_calls == 1:
            self._rotation_locked.set()
            if not self._release_rotation.wait(timeout=5):
                raise RuntimeError("rotation test timed out")
        return value


class LineChannelConcurrencyIntegrationTests(TransactionTestCase):
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

    def _register(self, public_id, channel_id, bot_user_id):
        service = DefaultLineChannelService(
            DjangoLineChannelRepository(),
            self.primary_cipher,
            uuid_factory=lambda: public_id,
        )
        result = service.register(
            RegisterLineChannel(
                channel_id,
                bot_user_id,
                "before",
                build_credential_pair("token-before", "secret-before"),
                True,
            )
        )
        self.assertEqual(result.status, "succeeded")

    # テストケース: metadata更新と資格情報置換を独立connectionで競合させる
    # 期待値: 最新値へ収束し、metadata・完全pair・別チャネルを失わない
    def test_concurrent_metadata_and_credential_updates_preserve_latest_state(self):
        public_id = uuid.UUID("00000000-0000-4000-8000-000000000021")
        untouched_id = uuid.UUID("00000000-0000-4000-8000-000000000022")
        self._register(public_id, "123456789021", "U" + "2" * 32)
        self._register(untouched_id, "123456789022", "U" + "3" * 32)
        untouched_before = LineChannel.objects.get(public_id=untouched_id)
        target_before = LineChannel.objects.get(public_id=public_id)
        credential_before = LineChannelCredential.objects.get(
            line_channel__public_id=public_id
        )
        old_access_ciphertext = bytes(credential_before.access_token_ciphertext)
        old_secret_ciphertext = bytes(credential_before.channel_secret_ciphertext)
        first_locked = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()

        first_service = DefaultLineChannelService(
            _BlockingLineChannelRepository(first_locked, release_first),
            self.primary_cipher,
        )
        second_service = DefaultLineChannelService(
            DjangoLineChannelRepository(), self.primary_cipher
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                _run_with_independent_connection,
                lambda: first_service.update(
                    UpdateLineChannel(public_id, bot_user_id="U" + "4" * 32)
                ),
            )
            self.assertTrue(first_locked.wait(timeout=5))

            def replace_credentials():
                second_started.set()
                return second_service.update(
                    UpdateLineChannel(
                        public_id,
                        credentials=build_credential_pair(
                            "token-after-concurrent-update",
                            "secret-after-concurrent-update",
                        ),
                    )
                )

            second = executor.submit(
                _run_with_independent_connection,
                replace_credentials,
            )
            self.assertTrue(second_started.wait(timeout=5))
            release_first.set()
            results = (first.result(timeout=5), second.result(timeout=5))

        channel = LineChannel.objects.get(public_id=public_id)
        untouched_after = LineChannel.objects.get(public_id=untouched_id)
        credential_before.refresh_from_db()
        self.assertTrue(all(result.status == "succeeded" for result in results))
        self.assertEqual(channel.bot_user_id, "U" + "4" * 32)
        self.assertEqual(channel.label, "before")
        self.assertGreater(channel.updated_at, target_before.updated_at)
        self.assertEqual(untouched_after.updated_at, untouched_before.updated_at)
        self.assertNotEqual(
            bytes(credential_before.access_token_ciphertext), old_access_ciphertext
        )
        self.assertNotEqual(
            bytes(credential_before.channel_secret_ciphertext), old_secret_ciphertext
        )
        repository = DjangoCredentialRepository(self.primary_cipher)
        self.assertEqual(
            repository.get_access_token(public_id).value.reveal_for_use(),
            "token-after-concurrent-update",
        )
        self.assertEqual(
            repository.get_channel_secret(public_id).value.reveal_for_use(),
            "secret-after-concurrent-update",
        )

    # テストケース: rotationと通常資格情報更新を独立connectionで競合させる
    # 期待値: final sweepが最新pairを上書きせずprimary-only検証する
    def test_rotation_final_sweep_observes_concurrent_credential_update(self):
        public_id = uuid.UUID("00000000-0000-4000-8000-000000000031")
        channel = LineChannel.objects.create(
            public_id=public_id,
            messaging_api_channel_id="123456789031",
            bot_user_id="U" + "5" * 32,
            label="rotation concurrent",
            is_active=True,
        )
        old_pair = EncryptedCredentialPair(
            self.old_cipher.encrypt(
                AccessToken("old-token"),
                CredentialContext(public_id, "access_token"),
            ),
            self.old_cipher.encrypt(
                ChannelSecret("old-secret"),
                CredentialContext(public_id, "channel_secret"),
            ),
        )
        LineChannelCredential.objects.create(
            line_channel=channel,
            access_token_ciphertext=old_pair.access_token.ciphertext,
            channel_secret_ciphertext=old_pair.channel_secret.ciphertext,
        )

        rotation_locked = threading.Event()
        release_rotation = threading.Event()
        update_done = threading.Event()
        rotation_repository = _CoordinatedRotationRepository(
            rotation_locked, release_rotation, update_done
        )
        rotation_service = DefaultCredentialRotationService(
            self.rotation_cipher,
            rotation_repository,
            MySQLRotationLock(),
            DefaultCredentialRotationItemProcessor(self.rotation_cipher),
        )
        update_service = DefaultLineChannelService(
            DjangoLineChannelRepository(), self.rotation_cipher
        )

        def replace_credentials():
            result = update_service.update(
                UpdateLineChannel(
                    public_id,
                    credentials=build_credential_pair("new-token", "new-secret"),
                )
            )
            update_done.set()
            return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            rotation = executor.submit(
                _run_with_independent_connection, rotation_service.rotate_all
            )
            self.assertTrue(rotation_locked.wait(timeout=5))
            update = executor.submit(
                _run_with_independent_connection, replace_credentials
            )
            release_rotation.set()
            summary = rotation.result(timeout=10)
            update_result = update.result(timeout=10)

        repository = DjangoCredentialRepository(self.rotation_cipher)
        token = repository.get_access_token(public_id)
        secret = repository.get_channel_secret(public_id)
        self.assertEqual(update_result.status, "succeeded")
        self.assertEqual(summary.status, "complete")
        self.assertTrue(summary.old_keys_removable)
        self.assertEqual(token.value.reveal_for_use(), "new-token")
        self.assertEqual(secret.value.reveal_for_use(), "new-secret")
