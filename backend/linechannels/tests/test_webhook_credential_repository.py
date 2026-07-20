from dataclasses import FrozenInstanceError, fields
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase

from linechannels.crypto import CredentialCryptoError
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialUnavailable,
    EncryptedCredential,
    WebhookChannelAvailable,
)


class WebhookChannelResultTests(SimpleTestCase):
    # テストケース: Webhook 用の有効チャネル結果を構築して field を調べる
    # 期待値: 公開 ID、bot user ID、redacted secret 以外の資格情報を持たない
    def test_available_result_has_only_webhook_verification_material(self) -> None:
        result = WebhookChannelAvailable(
            channel_public_id=uuid4(),
            bot_user_id="U" + "1" * 32,
            channel_secret=ChannelSecret("secret-canary"),
        )

        self.assertEqual(
            {field.name for field in fields(result)},
            {"channel_public_id", "bot_user_id", "channel_secret", "status"},
        )
        self.assertNotIn("secret-canary", repr(result))
        self.assertNotIn("secret-canary", str(result))
        with self.assertRaises(FrozenInstanceError):
            result.bot_user_id = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            WebhookChannelAvailable(
                channel_public_id=uuid4(),
                bot_user_id="U" + "1" * 32,
                channel_secret=ChannelSecret("secret"),
                access_token=AccessToken("token"),  # type: ignore[call-arg]
            )

    # テストケース: Webhook 資格情報の安全な失敗結果を生成する
    # 期待値: 既存の内容非露出 CredentialUnavailable として識別できる
    def test_unavailable_result_reuses_safe_failure_contract(self) -> None:
        result = CredentialUnavailable("credential_unreadable")

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.code, "credential_unreadable")


class _RecordingCipher:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[EncryptedCredential, object]] = []

    def decrypt(self, value: EncryptedCredential, context: object) -> object:
        self.calls.append((value, context))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class WebhookCredentialRepositoryTests(TestCase):
    def _create_channel(
        self,
        *,
        is_active: bool = True,
        with_credentials: bool = True,
    ) -> LineChannel:
        channel = LineChannel.objects.create(
            messaging_api_channel_id="1234567890",
            bot_user_id="U" + "1" * 32,
            label="Webhook channel",
            is_active=is_active,
        )
        if with_credentials:
            LineChannelCredential.objects.create(
                line_channel=channel,
                access_token_ciphertext=b"access-token-ciphertext",
                channel_secret_ciphertext=b"channel-secret-ciphertext",
            )
        return channel

    # テストケース: active channel の Webhook 資格情報を一度取得する
    # 期待値: bot ID と同じ行の secret を返し access token は復号しない
    def test_get_returns_active_channel_snapshot_and_decrypts_only_secret(self) -> None:
        from linechannels.repositories import DjangoWebhookCredentialRepository

        channel = self._create_channel()
        cipher = _RecordingCipher(ChannelSecret("plain-channel-secret"))
        repository = DjangoWebhookCredentialRepository(cipher)  # type: ignore[arg-type]

        with self.assertNumQueries(1):
            result = repository.get(channel.public_id)

        self.assertIsInstance(result, WebhookChannelAvailable)
        self.assertEqual(result.channel_public_id, channel.public_id)  # type: ignore[union-attr]
        self.assertEqual(result.bot_user_id, channel.bot_user_id)  # type: ignore[union-attr]
        self.assertEqual(len(cipher.calls), 1)
        encrypted, context = cipher.calls[0]
        self.assertEqual(encrypted.ciphertext, b"channel-secret-ciphertext")
        self.assertEqual(context.kind, "channel_secret")  # type: ignore[attr-defined]

    # テストケース: unknown、inactive、資格情報欠落の各 channel を取得する
    # 期待値: 秘密値や内部状態を持たない既存の unavailable 分類へ収束する
    def test_get_classifies_unavailable_channels_safely(self) -> None:
        from linechannels.repositories import DjangoWebhookCredentialRepository

        inactive = self._create_channel(is_active=False)
        missing = LineChannel.objects.create(
            messaging_api_channel_id="1234567891",
            bot_user_id="U" + "2" * 32,
            label="Missing",
            is_active=True,
        )
        cipher = _RecordingCipher(ChannelSecret("unused"))
        repository = DjangoWebhookCredentialRepository(cipher)  # type: ignore[arg-type]

        unknown_result = repository.get(uuid4())
        inactive_result = repository.get(inactive.public_id)
        missing_result = repository.get(missing.public_id)

        self.assertEqual(unknown_result, CredentialUnavailable("channel_not_found"))
        self.assertEqual(inactive_result, CredentialUnavailable("channel_inactive"))
        self.assertEqual(missing_result, CredentialUnavailable("credentials_incomplete"))
        self.assertEqual(cipher.calls, [])

    # テストケース: secret の復号が失敗または誤った型を返す
    # 期待値: 生例外を出さず credential_unreadable に変換する
    def test_get_classifies_corrupt_secret_safely(self) -> None:
        from linechannels.repositories import DjangoWebhookCredentialRepository

        channel = self._create_channel()
        corrupt = DjangoWebhookCredentialRepository(
            _RecordingCipher(CredentialCryptoError("credential_unreadable"))  # type: ignore[arg-type]
        )
        wrong_type = DjangoWebhookCredentialRepository(
            _RecordingCipher(AccessToken("wrong-kind"))  # type: ignore[arg-type]
        )

        self.assertEqual(
            corrupt.get(channel.public_id),
            CredentialUnavailable("credential_unreadable"),
        )
        self.assertEqual(
            wrong_type.get(channel.public_id),
            CredentialUnavailable("credential_unreadable"),
        )

    # テストケース: Webhook 資格情報 snapshot の保存層読取りが失敗する
    # 期待値: DB 例外を露出せず既存の安全な unavailable 分類へ収束する
    def test_get_classifies_database_failure_safely(self) -> None:
        from linechannels.repositories import DjangoWebhookCredentialRepository

        repository = DjangoWebhookCredentialRepository(  # type: ignore[arg-type]
            _RecordingCipher(ChannelSecret("unused"))
        )

        with patch.object(
            LineChannel.objects,
            "using",
            side_effect=DatabaseError("database-canary"),
        ):
            result = repository.get(uuid4())

        self.assertEqual(result, CredentialUnavailable("credential_unreadable"))
