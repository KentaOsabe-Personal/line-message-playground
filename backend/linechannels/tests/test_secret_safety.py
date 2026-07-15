import json
import pickle
from dataclasses import asdict

from django.test import SimpleTestCase

from linechannels.types import (
    AccessToken,
    ChannelMutationFailed,
    ChannelSecret,
    CredentialPair,
    EncryptedCredential,
    EncryptedCredentialPair,
)


class SecretSafetyTests(SimpleTestCase):
    # テストケース: 平文資格情報 wrapper を文字列化し、明示的な利用境界で取り出す
    # 期待値: str/repr は値を隠し、reveal_for_use だけが元の値を返す
    def test_plaintext_wrappers_only_reveal_through_explicit_method(self):
        token_canary = "access-token-canary"
        secret_canary = "channel-secret-canary"
        token = AccessToken(token_canary)
        secret = ChannelSecret(secret_canary)

        self.assertNotIn(token_canary, str(token))
        self.assertNotIn(token_canary, repr(token))
        self.assertNotIn(secret_canary, str(secret))
        self.assertNotIn(secret_canary, repr(secret))
        self.assertEqual(token.reveal_for_use(), token_canary)
        self.assertEqual(secret.reveal_for_use(), secret_canary)

    # テストケース: 秘密 wrapper と資格情報 pair を汎用シリアライザへ渡す
    # 期待値: 内部値を列挙できず、JSON/dataclass 変換も拒否される
    def test_secret_contracts_reject_implicit_serialization(self):
        token = AccessToken("token-canary")
        secret = ChannelSecret("secret-canary")
        encrypted = EncryptedCredential(b"ciphertext-canary")
        pair = CredentialPair(token, secret)
        encrypted_pair = EncryptedCredentialPair(encrypted, encrypted)

        with self.assertRaises(TypeError):
            vars(pair.access_token)
        with self.assertRaises(TypeError):
            json.dumps(pair)
        with self.assertRaises(TypeError):
            asdict(pair)
        for value in (token, secret, encrypted, pair, encrypted_pair):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(TypeError, "serialization is disabled"):
                    pickle.dumps(value)

    # テストケース: 暗号文と暗号文 pair を文字列化する
    # 期待値: str/repr に暗号文が含まれず、安全な型名だけが得られる
    def test_encrypted_contracts_do_not_expose_ciphertext(self):
        ciphertext_canary = b"ciphertext-canary"
        encrypted = EncryptedCredential(ciphertext_canary)
        pair = EncryptedCredentialPair(encrypted, EncryptedCredential(ciphertext_canary))

        for value in (encrypted, pair):
            self.assertNotIn(ciphertext_canary.decode(), str(value))
            self.assertNotIn(ciphertext_canary.decode(), repr(value))

    # テストケース: 下位例外の代わりに安全な mutation failure を文字列化する
    # 期待値: 公開された失敗分類だけが含まれ、秘密を保持する field は存在しない
    def test_failure_result_contains_only_safe_classification(self):
        result = ChannelMutationFailed(code="invalid_input")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, "invalid_input")
        self.assertNotIn("secret", repr(result).lower())
