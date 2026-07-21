import uuid

from django.test import TransactionTestCase

from linechannels.crypto import CredentialCryptoError
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.repositories import DjangoLineChannelRepository
from linechannels.services import DefaultLineChannelService
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialPair,
    EncryptedCredential,
    RegisterLineChannel,
    UpdateLineChannel,
)
from linechannels.validators import build_credential_pair


class RecordingCipher:
    def __init__(self, *, fail_on_call=None, unreadable=False):
        self.calls = []
        self.fail_on_call = fail_on_call
        self.unreadable = unreadable

    def encrypt(self, value, context):
        self.calls.append((value, context))
        if len(self.calls) == self.fail_on_call:
            raise CredentialCryptoError("encryption_failed")
        return EncryptedCredential(f"cipher-{context.kind}".encode())

    def decrypt(self, value, context):
        self.calls.append((value, context))
        if self.unreadable:
            raise CredentialCryptoError("credential_unreadable")
        if context.kind == "access_token":
            return AccessToken("decrypted-token")
        return ChannelSecret("decrypted-secret")

    def decrypt_with_primary(self, value, context):
        self.calls.append((value, context))
        if self.unreadable:
            raise CredentialCryptoError("credential_unreadable")
        if context.kind == "access_token":
            return AccessToken("token-replacement")
        return ChannelSecret("secret-replacement")


class DefaultLineChannelServiceRegisterTests(TransactionTestCase):
    def setUp(self):
        self.public_id = uuid.uuid4()
        self.cipher = RecordingCipher()
        self.service = DefaultLineChannelService(
            DjangoLineChannelRepository(),
            self.cipher,
            uuid_factory=lambda: self.public_id,
        )

    def command(self, **overrides):
        values = {
            "messaging_api_channel_id": "1234567890",
            "bot_user_id": "U" + "1" * 32,
            "label": "メインチャネル",
            "credentials": build_credential_pair("token-canary", "secret-canary"),
            "is_active": True,
            "provider_id": "000123",
        }
        values.update(overrides)
        return RegisterLineChannel(**values)

    # テストケース: 有効なチャネル情報と完全な資格情報ペアを登録する
    # 期待値: 2秘密を暗号化した後だけチャネルと資格情報が同時に作成される
    def test_register_encrypts_both_secrets_before_atomic_aggregate_creation(self):
        result = self.service.register(self.command())

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.channel.public_id, self.public_id)
        self.assertTrue(result.channel.credentials_configured)
        self.assertEqual(LineChannel.objects.count(), 1)
        self.assertEqual(LineChannelCredential.objects.count(), 1)
        credential = LineChannelCredential.objects.get()
        self.assertEqual(bytes(credential.access_token_ciphertext), b"cipher-access_token")
        self.assertEqual(bytes(credential.channel_secret_ciphertext), b"cipher-channel_secret")
        self.assertEqual([call[1].kind for call in self.cipher.calls], ["access_token", "channel_secret"])

    # テストケース: 2件目の秘密の暗号化で失敗する
    # 期待値: 安全な暗号化失敗を返し、どちらのテーブルにも部分行を残さない
    def test_second_encryption_failure_leaves_both_tables_empty(self):
        service = DefaultLineChannelService(
            DjangoLineChannelRepository(),
            RecordingCipher(fail_on_call=2),
            uuid_factory=lambda: self.public_id,
        )

        result = service.register(self.command())

        self.assertEqual((result.status, result.code), ("failed", "encryption_failed"))
        self.assertFalse(LineChannel.objects.exists())
        self.assertFalse(LineChannelCredential.objects.exists())
        self.assertNotIn("token-canary", repr(result))
        self.assertNotIn("secret-canary", repr(result))

    # テストケース: 登録済みのMessaging API channel IDを再登録する
    # 期待値: 安全な重複結果を返し、既存aggregateを上書きせず部分行も作らない
    def test_duplicate_is_safe_and_does_not_create_partial_credentials(self):
        first = self.service.register(self.command())
        duplicate = self.service.register(
            self.command(bot_user_id="U" + "2" * 32)
        )

        self.assertEqual(first.status, "succeeded")
        self.assertEqual((duplicate.status, duplicate.code), ("failed", "duplicate_channel"))
        self.assertEqual(LineChannel.objects.count(), 1)
        self.assertEqual(LineChannelCredential.objects.count(), 1)

    # テストケース: 内部wrapper型が不正な資格情報ペアを登録境界へ渡す
    # 期待値: 暗号処理やDBへ到達する前に安全な不正入力として拒否される
    def test_malformed_credential_pair_is_rejected_before_cipher_or_database(self):
        malformed = CredentialPair("token-canary", "secret-canary")  # type: ignore[arg-type]

        result = self.service.register(self.command(credentials=malformed))

        self.assertEqual((result.status, result.code), ("failed", "invalid_input"))
        self.assertEqual(self.cipher.calls, [])
        self.assertFalse(LineChannel.objects.exists())
        self.assertFalse(LineChannelCredential.objects.exists())


class DefaultLineChannelServiceUpdateTests(TransactionTestCase):
    def setUp(self):
        self.public_id = uuid.uuid4()
        self.cipher = RecordingCipher()
        self.service = DefaultLineChannelService(
            DjangoLineChannelRepository(),
            self.cipher,
            uuid_factory=lambda: self.public_id,
        )
        registered = self.service.register(
            RegisterLineChannel(
                messaging_api_channel_id="1234567890",
                bot_user_id="U" + "1" * 32,
                label="登録時名称",
                credentials=build_credential_pair("token-canary", "secret-canary"),
                is_active=True,
                provider_id="000123",
            )
        )
        self.assertEqual(registered.status, "succeeded")
        self.cipher.calls.clear()

    # テストケース: 登録済みチャネルの名称だけを更新する
    # 期待値: 公開UUID、未指定metadata、資格情報を維持し、更新日時だけを進める
    def test_update_changes_only_specified_metadata_and_preserves_public_id(self):
        before = LineChannel.objects.get(public_id=self.public_id)
        credential = LineChannelCredential.objects.get(line_channel=before)
        ciphertexts = (
            bytes(credential.access_token_ciphertext),
            bytes(credential.channel_secret_ciphertext),
        )

        result = self.service.update(
            UpdateLineChannel(self.public_id, label="更新後名称")
        )

        after = LineChannel.objects.get(public_id=self.public_id)
        credential.refresh_from_db()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(after.public_id, self.public_id)
        self.assertEqual(after.messaging_api_channel_id, "1234567890")
        self.assertEqual(after.bot_user_id, "U" + "1" * 32)
        self.assertEqual(after.label, "更新後名称")
        self.assertEqual(after.provider_id, "000123")
        self.assertGreater(after.updated_at, before.updated_at)
        self.assertEqual(
            (bytes(credential.access_token_ciphertext), bytes(credential.channel_secret_ciphertext)),
            ciphertexts,
        )

    # テストケース: provider未設定のlegacyチャネルへ検証済みproviderを指定する
    # 期待値: 登録時のprovider必須契約を維持しつつ、legacy値だけを一度補完できる
    def test_provider_is_required_for_register_and_can_backfill_legacy_channel(self):
        missing_provider = self.service.register(
            RegisterLineChannel(
                messaging_api_channel_id="999",
                bot_user_id="U" + "9" * 32,
                label="providerなし",
                credentials=build_credential_pair("token", "secret"),
                is_active=True,
                provider_id=None,
            )
        )
        channel = LineChannel.objects.get(public_id=self.public_id)
        channel.provider_id = None
        channel.save(update_fields=("provider_id",))

        backfilled = self.service.update(
            UpdateLineChannel(self.public_id, provider_id="000456")
        )

        channel.refresh_from_db()
        self.assertEqual(
            (missing_provider.status, missing_provider.code),
            ("failed", "invalid_input"),
        )
        self.assertEqual(backfilled.status, "succeeded")
        self.assertEqual(channel.provider_id, "000456")

    # テストケース: 設定済みproviderと同じproviderを再指定する
    # 期待値: 冪等な更新として成功し、providerと他属性を維持する
    def test_same_provider_update_is_idempotent(self):
        result = self.service.update(
            UpdateLineChannel(self.public_id, provider_id="000123")
        )

        channel = LineChannel.objects.get(public_id=self.public_id)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(channel.provider_id, "000123")
        self.assertEqual(channel.label, "登録時名称")
        self.assertTrue(channel.is_active)

    # テストケース: 設定済みproviderと異なるproviderを他の変更と同時指定する
    # 期待値: invalid_transitionとして拒否し、provider・metadata・資格情報をすべて不変に保つ
    def test_different_provider_update_rejects_all_requested_mutations(self):
        before = LineChannel.objects.get(public_id=self.public_id)
        credential = LineChannelCredential.objects.get(line_channel=before)
        ciphertexts = (
            bytes(credential.access_token_ciphertext),
            bytes(credential.channel_secret_ciphertext),
        )

        result = self.service.update(
            UpdateLineChannel(
                self.public_id,
                provider_id="000456",
                label="保存してはいけない",
                is_active=False,
                credentials=build_credential_pair(
                    "replacement-token", "replacement-secret"
                ),
            )
        )

        after = LineChannel.objects.get(public_id=self.public_id)
        credential.refresh_from_db()
        self.assertEqual(
            (result.status, result.code),
            ("failed", "invalid_transition"),
        )
        self.assertEqual(after.provider_id, "000123")
        self.assertEqual(after.label, "登録時名称")
        self.assertTrue(after.is_active)
        self.assertEqual(
            (
                bytes(credential.access_token_ciphertext),
                bytes(credential.channel_secret_ciphertext),
            ),
            ciphertexts,
        )
        self.assertEqual(self.cipher.calls, [])

    # テストケース: チャネルを無効化した後、保存済み資格情報だけで再有効化する
    # 期待値: 暗号文を保持し、両用途の復号検証に成功した同じチャネルを有効へ戻す
    def test_disable_preserves_credentials_and_enable_validates_both_saved_values(self):
        disabled = self.service.set_active(self.public_id, False)
        credential = LineChannelCredential.objects.get(line_channel__public_id=self.public_id)
        ciphertexts = (
            bytes(credential.access_token_ciphertext),
            bytes(credential.channel_secret_ciphertext),
        )

        enabled = self.service.set_active(self.public_id, True)

        channel = LineChannel.objects.get(public_id=self.public_id)
        credential.refresh_from_db()
        self.assertEqual(disabled.status, "succeeded")
        self.assertEqual(enabled.status, "succeeded")
        self.assertTrue(channel.is_active)
        self.assertEqual(
            (bytes(credential.access_token_ciphertext), bytes(credential.channel_secret_ciphertext)),
            ciphertexts,
        )
        self.assertEqual([call[1].kind for call in self.cipher.calls], ["access_token", "channel_secret"])

    # テストケース: 読み取れない保存済み資格情報で名称更新と有効化を同時指定する
    # 期待値: 安全な読取不能結果を返し、名称と有効状態の変更をすべてrollbackする
    def test_unreadable_saved_credentials_roll_back_metadata_and_enable(self):
        self.service.set_active(self.public_id, False)
        failing = DefaultLineChannelService(
            DjangoLineChannelRepository(), RecordingCipher(unreadable=True)
        )

        result = failing.update(
            UpdateLineChannel(self.public_id, label="保存してはいけない", is_active=True)
        )

        channel = LineChannel.objects.get(public_id=self.public_id)
        self.assertEqual((result.status, result.code), ("failed", "credential_unreadable"))
        self.assertEqual(channel.label, "登録時名称")
        self.assertFalse(channel.is_active)

    # テストケース: 破損した旧資格情報を新しい完全なペアで置換しながら有効化する
    # 期待値: 新ペアをprimary-onlyで検証し、名称・資格情報・有効状態を同時に保存する
    def test_new_credentials_can_replace_corrupt_pair_while_enabling(self):
        self.service.set_active(self.public_id, False)
        LineChannelCredential.objects.filter(line_channel__public_id=self.public_id).update(
            access_token_ciphertext=b"corrupt-access",
            channel_secret_ciphertext=b"corrupt-secret",
        )
        self.cipher.calls.clear()

        result = self.service.update(
            UpdateLineChannel(
                self.public_id,
                label="復旧後",
                credentials=build_credential_pair(
                    "token-replacement", "secret-replacement"
                ),
                is_active=True,
            )
        )

        channel = LineChannel.objects.get(public_id=self.public_id)
        credential = LineChannelCredential.objects.get(line_channel=channel)
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(channel.is_active)
        self.assertEqual(channel.label, "復旧後")
        self.assertEqual(bytes(credential.access_token_ciphertext), b"cipher-access_token")
        self.assertEqual(bytes(credential.channel_secret_ciphertext), b"cipher-channel_secret")
        self.assertEqual(
            [call[1].kind for call in self.cipher.calls],
            ["access_token", "channel_secret", "access_token", "channel_secret"],
        )

    # テストケース: 存在しない公開UUIDの更新と、変更項目のない更新を要求する
    # 期待値: 暗黙作成や既存チャネル変更を行わず、それぞれ安全な失敗へ分類する
    def test_not_found_and_empty_update_do_not_create_or_change_channels(self):
        missing = self.service.update(
            UpdateLineChannel(uuid.uuid4(), label="作成されない")
        )
        empty = self.service.update(UpdateLineChannel(self.public_id))

        self.assertEqual((missing.status, missing.code), ("failed", "channel_not_found"))
        self.assertEqual((empty.status, empty.code), ("failed", "invalid_input"))
        self.assertEqual(LineChannel.objects.count(), 1)
