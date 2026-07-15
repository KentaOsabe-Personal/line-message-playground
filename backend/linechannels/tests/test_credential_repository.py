import uuid
from unittest.mock import patch

from django.db import DatabaseError
from django.db.models.query import QuerySet
from django.test import TestCase

from linechannels.crypto import CredentialCryptoError
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.repositories import (
    CredentialRepository,
    DjangoCredentialRepository,
)
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialAvailable,
    CredentialUnavailable,
)


class SpyCredentialCipher:
    def __init__(self):
        self.calls = []
        self.failure = None

    def decrypt(self, value, context):
        self.calls.append((value.ciphertext, context))
        if self.failure is not None:
            raise self.failure
        if context.kind == "access_token":
            return AccessToken("decrypted-access-token-canary")
        return ChannelSecret("decrypted-channel-secret-canary")


class DjangoCredentialRepositoryTests(TestCase):
    def setUp(self):
        self.cipher = SpyCredentialCipher()
        self.repository = DjangoCredentialRepository(self.cipher)

    def make_channel(self, *, active=True, credentials=True):
        suffix = uuid.uuid4().hex
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(int(suffix[:12], 16)),
            bot_user_id=f"U{suffix}",
            label="用途別取得検証用",
            is_active=active,
        )
        if credentials:
            LineChannelCredential.objects.create(
                line_channel=channel,
                access_token_ciphertext=b"stored-access-ciphertext-canary",
                channel_secret_ciphertext=b"stored-secret-ciphertext-canary",
            )
        return channel

    # テストケース: Django具象repositoryを用途別取得の公開contractとして扱う
    # 期待値: CredentialRepository Protocolへ構造的に適合する
    def test_concrete_repository_implements_public_protocol(self):
        self.assertIsInstance(self.repository, CredentialRepository)

    # テストケース: 有効チャネルの送信用資格情報を取得する
    # 期待値: access token列だけを復号しAccessToken wrapperで返す
    def test_get_access_token_decrypts_only_access_token_column(self):
        channel = self.make_channel()

        result = self.repository.get_access_token(channel.public_id)

        self.assertIsInstance(result, CredentialAvailable)
        self.assertIsInstance(result.value, AccessToken)
        self.assertEqual(result.value.reveal_for_use(), "decrypted-access-token-canary")
        self.assertEqual(len(self.cipher.calls), 1)
        ciphertext, context = self.cipher.calls[0]
        self.assertEqual(ciphertext, b"stored-access-ciphertext-canary")
        self.assertNotEqual(ciphertext, b"stored-secret-ciphertext-canary")
        self.assertEqual(context.channel_public_id, channel.public_id)
        self.assertEqual(context.kind, "access_token")

    # テストケース: 有効チャネルのWebhook検証用資格情報を取得する
    # 期待値: channel secret列だけを復号しChannelSecret wrapperで返す
    def test_get_channel_secret_decrypts_only_channel_secret_column(self):
        channel = self.make_channel()

        result = self.repository.get_channel_secret(channel.public_id)

        self.assertIsInstance(result, CredentialAvailable)
        self.assertIsInstance(result.value, ChannelSecret)
        self.assertEqual(
            result.value.reveal_for_use(),
            "decrypted-channel-secret-canary",
        )
        self.assertEqual(len(self.cipher.calls), 1)
        ciphertext, context = self.cipher.calls[0]
        self.assertEqual(ciphertext, b"stored-secret-ciphertext-canary")
        self.assertNotEqual(ciphertext, b"stored-access-ciphertext-canary")
        self.assertEqual(context.channel_public_id, channel.public_id)
        self.assertEqual(context.kind, "channel_secret")

    # テストケース: 不存在、無効、資格情報欠損のチャネルから秘密を取得する
    # 期待値: 復号前に安全なunavailable codeへ分類する
    def test_unavailable_channel_states_are_classified_before_decryption(self):
        inactive = self.make_channel(active=False)
        incomplete = self.make_channel(credentials=False)
        cases = (
            (uuid.uuid4(), "channel_not_found"),
            (inactive.public_id, "channel_inactive"),
            (incomplete.public_id, "credentials_incomplete"),
        )

        for public_id, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                result = self.repository.get_access_token(public_id)
                self.assertIsInstance(result, CredentialUnavailable)
                self.assertEqual(result.code, expected_code)
        self.assertEqual(self.cipher.calls, [])

    # テストケース: 保存済み暗号文の復号・完全性検証が失敗する
    # 期待値: raw crypto例外を返さず復旧が必要なunavailable結果へ置換する
    def test_crypto_failure_is_classified_as_credential_unreadable(self):
        channel = self.make_channel()
        self.cipher.failure = CredentialCryptoError("credential_unreadable")

        result = self.repository.get_access_token(channel.public_id)

        self.assertIsInstance(result, CredentialUnavailable)
        self.assertEqual(result.code, "credential_unreadable")

    # テストケース: ORM読込がraw database errorで失敗する
    # 期待値: DB詳細を返さずcredential_unreadableの安全な結果へ置換する
    def test_database_failure_does_not_escape_repository_contract(self):
        canary = "raw-database-error-canary"

        with patch.object(
            QuerySet,
            "values",
            side_effect=DatabaseError(canary),
        ):
            result = self.repository.get_access_token(uuid.uuid4())

        self.assertIsInstance(result, CredentialUnavailable)
        self.assertEqual(result.code, "credential_unreadable")
        self.assertNotIn(canary, str(result))
        self.assertNotIn(canary, repr(result))

    # テストケース: 用途別取得結果を文字列化する
    # 期待値: 平文と暗号文がreprまたはstrへ現れない
    def test_results_do_not_expose_plaintext_or_ciphertext(self):
        channel = self.make_channel()

        result = self.repository.get_access_token(channel.public_id)
        combined = f"{result!s} {result!r}"

        self.assertNotIn("decrypted-access-token-canary", combined)
        self.assertNotIn("stored-access-ciphertext-canary", combined)
        self.assertNotIn("stored-secret-ciphertext-canary", combined)
