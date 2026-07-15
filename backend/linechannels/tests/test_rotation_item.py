import uuid

from cryptography.fernet import Fernet
from django.test import SimpleTestCase

from linechannels.crypto import FernetCredentialCipher, parse_credential_keyring
from linechannels.rotation_item import (
    CredentialRotationItemProcessor,
    DefaultCredentialRotationItemProcessor,
)
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialContext,
    EncryptedCredential,
    EncryptedCredentialPair,
)


class CredentialRotationItemProcessorTests(SimpleTestCase):
    def setUp(self):
        self.public_id = uuid.uuid4()
        self.primary_key = Fernet.generate_key()
        self.old_key = Fernet.generate_key()
        self.rotating_cipher = self.cipher(self.primary_key, self.old_key)
        self.primary_cipher = self.cipher(self.primary_key)
        self.old_cipher = self.cipher(self.old_key)
        self.processor = DefaultCredentialRotationItemProcessor(self.rotating_cipher)

    @staticmethod
    def cipher(*keys):
        return FernetCredentialCipher(
            parse_credential_keyring(",".join(key.decode("ascii") for key in keys))
        )

    def pair(self, cipher, public_id=None):
        target = public_id or self.public_id
        return EncryptedCredentialPair(
            cipher.encrypt(
                AccessToken("access-canary"),
                CredentialContext[AccessToken](target, "access_token"),
            ),
            cipher.encrypt(
                ChannelSecret("secret-canary"),
                CredentialContext[ChannelSecret](target, "channel_secret"),
            ),
        )

    # テストケース: 両資格情報がすでに現用鍵と期待contextで暗号化されている
    # 期待値: 暗号文を変更・返却せずverifiedとして分類する
    def test_primary_pair_is_verified_without_replacement(self):
        pair = self.pair(self.primary_cipher)

        result = self.processor.process(self.public_id, pair)

        self.assertEqual(result.status, "verified")
        self.assertFalse(hasattr(result, "credentials"))

    # テストケース: 両資格情報が旧鍵と期待contextで暗号化されている
    # 期待値: 現用鍵へ再暗号化し、元の2値をprimary-onlyで取得できるrotated pairを返す
    def test_old_pair_is_rotated_and_reverified_with_primary(self):
        pair = self.pair(self.old_cipher)

        result = self.processor.process(self.public_id, pair)

        self.assertEqual(result.status, "rotated")
        self.assertNotEqual(
            result.credentials.access_token.ciphertext,
            pair.access_token.ciphertext,
        )
        access = self.primary_cipher.decrypt_with_primary(
            result.credentials.access_token,
            CredentialContext[AccessToken](self.public_id, "access_token"),
        )
        secret = self.primary_cipher.decrypt_with_primary(
            result.credentials.channel_secret,
            CredentialContext[ChannelSecret](self.public_id, "channel_secret"),
        )
        self.assertEqual(access.reveal_for_use(), "access-canary")
        self.assertEqual(secret.reveal_for_use(), "secret-canary")

    # テストケース: pair片側が破損、別チャネル、または別用途へ差し替えられている
    # 期待値: 新しい暗号文pairを返さずcredential_unreadableだけを返す
    def test_corrupt_or_context_swapped_pair_fails_without_new_pair(self):
        valid = self.pair(self.old_cipher)
        other_channel = self.pair(self.old_cipher, uuid.uuid4())
        cases = (
            EncryptedCredentialPair(
                EncryptedCredential(b"corrupt"), valid.channel_secret
            ),
            EncryptedCredentialPair(
                other_channel.access_token, valid.channel_secret
            ),
            EncryptedCredentialPair(valid.channel_secret, valid.access_token),
        )

        for pair in cases:
            with self.subTest(pair=pair):
                result = self.processor.process(self.public_id, pair)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.code, "credential_unreadable")
                self.assertFalse(hasattr(result, "credentials"))

    # テストケース: 再暗号化後のprimary-only照合が異なる平文を返す
    # 期待値: 検証済みでない新pairを破棄しverification_failedだけを返す
    def test_reverification_mismatch_discards_rotated_pair(self):
        cipher = ReverificationMismatchCipher(self.rotating_cipher)
        processor = DefaultCredentialRotationItemProcessor(cipher)

        result = processor.process(self.public_id, self.pair(self.old_cipher))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, "verification_failed")
        self.assertFalse(hasattr(result, "credentials"))

    # テストケース: final sweepで旧鍵pairをprimary-only検証する
    # 期待値: fallbackや再暗号化を一度も行わずfailedとして分類する
    def test_final_sweep_verification_never_falls_back_or_rotates(self):
        cipher = SpyCipher(self.rotating_cipher)
        processor = DefaultCredentialRotationItemProcessor(cipher)

        result = processor.verify_with_primary(
            self.public_id, self.pair(self.old_cipher)
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, "credential_unreadable")
        self.assertEqual(cipher.decrypt_calls, 0)
        self.assertEqual(cipher.rotate_calls, 0)

    # テストケース: 具象processorを1資格情報pairの公開contractとして扱う
    # 期待値: CredentialRotationItemProcessor Protocolへ構造的に適合する
    def test_concrete_processor_implements_public_protocol(self):
        self.assertIsInstance(self.processor, CredentialRotationItemProcessor)


class SpyCipher:
    def __init__(self, delegate):
        self.delegate = delegate
        self.decrypt_calls = 0
        self.rotate_calls = 0

    def decrypt_with_primary(self, value, context):
        return self.delegate.decrypt_with_primary(value, context)

    def decrypt(self, value, context):
        self.decrypt_calls += 1
        return self.delegate.decrypt(value, context)

    def rotate(self, value, context):
        self.rotate_calls += 1
        return self.delegate.rotate(value, context)


class ReverificationMismatchCipher(SpyCipher):
    def decrypt_with_primary(self, value, context):
        result = self.delegate.decrypt_with_primary(value, context)
        if self.rotate_calls and context.kind == "channel_secret":
            return ChannelSecret("different-value")
        return result
