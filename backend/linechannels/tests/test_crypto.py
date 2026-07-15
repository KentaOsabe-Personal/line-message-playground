import json
from uuid import uuid4

from cryptography.fernet import Fernet
from django.test import SimpleTestCase

from linechannels.crypto import (
    CredentialCryptoError,
    FernetCredentialCipher,
    parse_credential_keyring,
)
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialContext,
    EncryptedCredential,
)


class FernetCredentialCipherTests(SimpleTestCase):
    # テストケース: tokenとsecretを同じチャネルの別用途として暗号化・復号する
    # 期待値: 別暗号文となり、期待contextでだけ元の用途固有wrapperを取得できる
    def test_encrypts_credentials_as_separate_context_bound_envelopes(self):
        cipher, _ = self._cipher()
        public_id = uuid4()
        token_context = CredentialContext[AccessToken](public_id, "access_token")
        secret_context = CredentialContext[ChannelSecret](public_id, "channel_secret")

        encrypted_token = cipher.encrypt(AccessToken("token-value"), token_context)
        encrypted_secret = cipher.encrypt(ChannelSecret("secret-value"), secret_context)

        self.assertNotEqual(encrypted_token.ciphertext, encrypted_secret.ciphertext)
        self.assertEqual(
            cipher.decrypt(encrypted_token, token_context).reveal_for_use(),
            "token-value",
        )
        self.assertEqual(
            cipher.decrypt(encrypted_secret, secret_context).reveal_for_use(),
            "secret-value",
        )

    # テストケース: 暗号文を別チャネルまたは別用途のcontextへ差し替える
    # 期待値: 完全性エラーとなり、平文・暗号文・context値を返さない
    def test_rejects_channel_and_credential_kind_swaps(self):
        cipher, _ = self._cipher()
        public_id = uuid4()
        token_context = CredentialContext[AccessToken](public_id, "access_token")
        encrypted = cipher.encrypt(AccessToken("plaintext-canary"), token_context)
        wrong_contexts = (
            CredentialContext[AccessToken](uuid4(), "access_token"),
            CredentialContext[ChannelSecret](public_id, "channel_secret"),
        )

        for context in wrong_contexts:
            with self.subTest(kind=context.kind):
                with self.assertRaises(CredentialCryptoError) as raised:
                    cipher.decrypt(encrypted, context)
                rendered = repr(raised.exception)
                self.assertEqual(str(raised.exception), "credential_unreadable")
                self.assertNotIn("plaintext-canary", rendered)
                self.assertNotIn(str(public_id), rendered)

    # テストケース: 暗号文を改変または切り詰める
    # 期待値: 平文や部分値を返さず安全な完全性エラーになる
    def test_rejects_tampered_or_truncated_ciphertext(self):
        cipher, _ = self._cipher()
        context = CredentialContext[AccessToken](uuid4(), "access_token")
        encrypted = cipher.encrypt(AccessToken("plaintext-canary"), context)
        tampered = encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1])

        for ciphertext in (tampered, encrypted.ciphertext[:20], b""):
            with self.subTest(ciphertext_length=len(ciphertext)):
                with self.assertRaisesRegex(CredentialCryptoError, "credential_unreadable"):
                    cipher.decrypt(EncryptedCredential(ciphertext), context)

    # テストケース: 復号可能だがschema不正・未知versionのenvelopeを渡す
    # 期待値: JSON内容を露出せず安全な完全性エラーとして拒否する
    def test_rejects_invalid_or_unknown_envelopes(self):
        key = Fernet.generate_key()
        cipher = FernetCredentialCipher(parse_credential_keyring(key.decode("ascii")))
        public_id = uuid4()
        context = CredentialContext[AccessToken](public_id, "access_token")
        envelopes = (
            b"not-json-canary",
            self._envelope(public_id, "access_token", "value-canary", version=2),
            json.dumps(
                {
                    "format_version": 1,
                    "channel_public_id": str(public_id),
                    "credential_kind": "access_token",
                    "value": "value-canary",
                    "extra": "forbidden",
                }
            ).encode(),
            json.dumps(
                {
                    "format_version": 1,
                    "channel_public_id": str(public_id),
                    "credential_kind": "access_token",
                }
            ).encode(),
        )

        for envelope in envelopes:
            ciphertext = Fernet(key).encrypt(envelope)
            with self.subTest(envelope_length=len(envelope)):
                with self.assertRaises(CredentialCryptoError) as raised:
                    cipher.decrypt(EncryptedCredential(ciphertext), context)
                self.assertNotIn("canary", repr(raised.exception))

    # テストケース: 現用鍵と旧鍵を設定して旧鍵暗号文を通常readする
    # 期待値: 旧鍵readは成功し、新規writeは現用鍵だけで復号できる
    def test_reads_old_key_and_writes_only_with_primary_key(self):
        primary_key = Fernet.generate_key()
        old_key = Fernet.generate_key()
        rotating = FernetCredentialCipher(
            parse_credential_keyring(
                f"{primary_key.decode('ascii')},{old_key.decode('ascii')}"
            )
        )
        old_only = FernetCredentialCipher(parse_credential_keyring(old_key.decode("ascii")))
        primary_only = FernetCredentialCipher(
            parse_credential_keyring(primary_key.decode("ascii"))
        )
        context = CredentialContext[AccessToken](uuid4(), "access_token")
        old_ciphertext = old_only.encrypt(AccessToken("old-value"), context)
        new_ciphertext = rotating.encrypt(AccessToken("new-value"), context)

        self.assertEqual(
            rotating.decrypt(old_ciphertext, context).reveal_for_use(),
            "old-value",
        )
        self.assertEqual(
            primary_only.decrypt(new_ciphertext, context).reveal_for_use(),
            "new-value",
        )
        with self.assertRaises(CredentialCryptoError):
            old_only.decrypt(new_ciphertext, context)

    # テストケース: 旧鍵暗号文をprimary-only検証してから再暗号化する
    # 期待値: 検証は失敗し、rotate後だけ現用鍵で同じ値を取得できる
    def test_primary_verification_and_rotation(self):
        primary_key = Fernet.generate_key()
        old_key = Fernet.generate_key()
        rotating = FernetCredentialCipher(
            parse_credential_keyring(
                f"{primary_key.decode('ascii')},{old_key.decode('ascii')}"
            )
        )
        old_only = FernetCredentialCipher(parse_credential_keyring(old_key.decode("ascii")))
        context = CredentialContext[ChannelSecret](uuid4(), "channel_secret")
        old_ciphertext = old_only.encrypt(ChannelSecret("secret-value"), context)

        with self.assertRaises(CredentialCryptoError):
            rotating.decrypt_with_primary(old_ciphertext, context)
        rotated = rotating.rotate(old_ciphertext, context)

        self.assertEqual(
            rotating.decrypt_with_primary(rotated, context).reveal_for_use(),
            "secret-value",
        )

    # テストケース: 単一鍵と複数鍵のcipherでrotation readinessを取得する
    # 期待値: 鍵素材・鍵数なしのold_key_missing/ready分類だけを返す
    def test_reports_safe_rotation_readiness(self):
        single, _ = self._cipher()
        multi, _ = self._cipher(with_old_key=True)

        self.assertEqual(single.rotation_readiness(), "old_key_missing")
        self.assertEqual(multi.rotation_readiness(), "ready")
        self.assertNotIn("key", repr(multi.rotation_readiness()).lower())

    @staticmethod
    def _cipher(with_old_key: bool = False):
        keys = [Fernet.generate_key().decode("ascii")]
        if with_old_key:
            keys.append(Fernet.generate_key().decode("ascii"))
        raw = ",".join(keys)
        return FernetCredentialCipher(parse_credential_keyring(raw)), raw

    @staticmethod
    def _envelope(public_id, kind, value, version=1):
        return json.dumps(
            {
                "format_version": version,
                "channel_public_id": str(public_id),
                "credential_kind": kind,
                "value": value,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
