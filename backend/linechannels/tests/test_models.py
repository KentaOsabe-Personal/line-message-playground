import uuid

from django.db import IntegrityError, models, transaction
from django.forms import modelform_factory
from django.test import TestCase

from linechannels.models import LineChannel, LineChannelCredential


class LineChannelModelTests(TestCase):
    def make_channel(self, **overrides):
        suffix = uuid.uuid4().hex
        values = {
            "messaging_api_channel_id": str(int(suffix[:12], 16)),
            "bot_user_id": f"U{suffix}",
            "label": "検証用チャネル",
            "is_active": True,
        }
        values.update(overrides)
        return LineChannel.objects.create(**values)

    def make_credential(self, channel, **overrides):
        values = {
            "line_channel": channel,
            "access_token_ciphertext": b"access-token-ciphertext-canary",
            "channel_secret_ciphertext": b"channel-secret-ciphertext-canary",
        }
        values.update(overrides)
        return LineChannelCredential.objects.create(**values)

    # テストケース: 2つのLINEチャネルと完全な暗号文ペアを保存する
    # 期待値: 内部連番とは別の公開UUIDと独立した資格情報行が保存される
    def test_multiple_channels_store_complete_credential_pairs(self):
        first = self.make_channel(label="1番目")
        second = self.make_channel(label="2番目")

        first_credential = self.make_credential(first)
        second_credential = self.make_credential(second)

        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.public_id, second.public_id)
        self.assertEqual(first.public_id.version, 4)
        self.assertEqual(first_credential.pk, first.pk)
        self.assertEqual(second_credential.pk, second.pk)
        self.assertTrue(first.created_at)
        self.assertTrue(first.updated_at)
        self.assertTrue(first_credential.created_at)
        self.assertTrue(first_credential.updated_at)

    # テストケース: 公開UUID、Messaging API channel ID、bot user IDを重複保存する
    # 期待値: 各識別子のDB一意制約が2件目を拒否する
    def test_channel_identifiers_are_unique(self):
        existing = self.make_channel()

        for field_name in (
            "public_id",
            "messaging_api_channel_id",
            "bot_user_id",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.make_channel(**{field_name: getattr(existing, field_name)})

    # テストケース: 片側が空の暗号文ペアをDBへ保存する
    # 期待値: DB制約が不完全な資格情報行を拒否する
    def test_database_rejects_empty_ciphertext_in_either_column(self):
        for field_name in (
            "access_token_ciphertext",
            "channel_secret_ciphertext",
        ):
            with self.subTest(field_name=field_name):
                channel = self.make_channel()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.make_credential(channel, **{field_name: b""})

    # テストケース: 同じチャネルへ資格情報行を2件保存する
    # 期待値: 1対1主キー制約が2件目を拒否する
    def test_credentials_are_one_to_one_with_channel(self):
        channel = self.make_channel()
        self.make_credential(channel)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_credential(channel)

    # テストケース: 資格情報を保持するチャネルを物理削除する
    # 期待値: PROTECT関係が資格情報の連鎖削除を拒否する
    def test_credentials_protect_channel_from_physical_deletion(self):
        channel = self.make_channel()
        self.make_credential(channel)

        with self.assertRaises(models.ProtectedError):
            channel.delete()

    # テストケース: モデルとModelFormの公開表現を調べる
    # 期待値: 公開IDと状態だけを表示し、暗号文は表示も編集もできない
    def test_model_display_and_forms_do_not_expose_ciphertexts(self):
        channel = self.make_channel()
        credential = self.make_credential(channel)
        canaries = (
            bytes(credential.access_token_ciphertext).decode("ascii"),
            bytes(credential.channel_secret_ciphertext).decode("ascii"),
        )

        combined_display = " ".join(
            (str(channel), repr(channel), str(credential), repr(credential))
        )
        self.assertIn(str(channel.public_id), combined_display)
        self.assertIn("active=True", combined_display)
        self.assertIn("credentials_configured=True", combined_display)
        for canary in canaries:
            self.assertNotIn(canary, combined_display)

        form_class = modelform_factory(LineChannelCredential, fields="__all__")
        self.assertNotIn("access_token_ciphertext", form_class.base_fields)
        self.assertNotIn("channel_secret_ciphertext", form_class.base_fields)

    # テストケース: 永続化フィールドとindex設定を検査する
    # 期待値: 暗号文は非index・非編集で、active lookupだけがindex対象になる
    def test_schema_fields_follow_the_non_exposure_contract(self):
        public_id = LineChannel._meta.get_field("public_id")
        active = LineChannel._meta.get_field("is_active")
        credential_fields = (
            LineChannelCredential._meta.get_field("access_token_ciphertext"),
            LineChannelCredential._meta.get_field("channel_secret_ciphertext"),
        )

        self.assertTrue(public_id.unique)
        self.assertFalse(public_id.editable)
        self.assertTrue(active.db_index)
        for field in credential_fields:
            self.assertFalse(field.null)
            self.assertFalse(field.editable)
            self.assertFalse(field.db_index)
            self.assertFalse(field.unique)
