import uuid
from unittest.mock import patch

from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.db.models.query import QuerySet
from django.test import TransactionTestCase

from linechannels.models import LineChannel, LineChannelCredential
from linechannels.repositories import (
    DjangoLineChannelRepository,
    LineChannelRepository,
    LockedChannel,
    NewLineChannel,
    PersistedChannelMutation,
    PersistenceError,
    RepositoryProgrammingError,
)
from linechannels.types import EncryptedCredential, EncryptedCredentialPair


class DjangoLineChannelRepositoryTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.repository = DjangoLineChannelRepository()

    def make_new_channel(self, **overrides):
        suffix = uuid.uuid4().hex
        values = {
            "public_id": uuid.uuid4(),
            "messaging_api_channel_id": str(int(suffix[:12], 16)),
            "bot_user_id": f"U{suffix}",
            "label": "repository検証用",
            "is_active": True,
            "provider_id": "000123",
        }
        values.update(overrides)
        return NewLineChannel(**values)

    def make_encrypted_pair(self, marker=b"initial"):
        return EncryptedCredentialPair(
            EncryptedCredential(marker + b"-access"),
            EncryptedCredential(marker + b"-secret"),
        )

    def create_aggregate(self, **overrides):
        channel = self.make_new_channel(**overrides)
        credentials = self.make_encrypted_pair()
        with transaction.atomic():
            summary = self.repository.create_with_credentials(channel, credentials)
        return summary

    # テストケース: Django具象repositoryを通常mutationの公開contractとして扱う
    # 期待値: LineChannelRepository Protocolへ構造的に適合する
    def test_concrete_repository_implements_public_protocol(self):
        self.assertIsInstance(self.repository, LineChannelRepository)

    # テストケース: transaction外で通常永続化repositoryの各locked操作を呼ぶ
    # 期待値: DB変更前に安全なtransaction_requiredエラーで拒否される
    def test_locked_operations_require_caller_owned_transaction(self):
        channel = self.make_new_channel()
        credentials = self.make_encrypted_pair()
        locked = LockedChannel(
            public=self._summary_for_unsaved(channel),
            encrypted_credentials=credentials,
        )

        operations = (
            lambda: self.repository.create_with_credentials(channel, credentials),
            lambda: self.repository.get_for_update(channel.public_id),
            lambda: self.repository.update_locked(
                locked,
                PersistedChannelMutation(label="更新後"),
            ),
        )

        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(RepositoryProgrammingError) as captured:
                    operation()
                self.assertEqual(captured.exception.code, "transaction_required")
        self.assertFalse(LineChannel.objects.exists())

    # テストケース: caller transaction内でチャネルと資格情報を同時作成する
    # 期待値: 公開summaryと完全な暗号文ペアが同時にcommitされる
    def test_create_with_credentials_commits_complete_aggregate(self):
        channel = self.make_new_channel()
        credentials = self.make_encrypted_pair()

        with transaction.atomic():
            summary = self.repository.create_with_credentials(channel, credentials)

        stored = LineChannel.objects.get(public_id=channel.public_id)
        stored_credentials = LineChannelCredential.objects.get(line_channel=stored)
        self.assertEqual(summary.public_id, channel.public_id)
        self.assertTrue(summary.credentials_configured)
        self.assertEqual(
            bytes(stored_credentials.access_token_ciphertext),
            credentials.access_token.ciphertext,
        )
        self.assertEqual(
            bytes(stored_credentials.channel_secret_ciphertext),
            credentials.channel_secret.ciphertext,
        )

    # テストケース: aggregate作成後にcaller transactionをrollbackする
    # 期待値: channelとcredentialのどちらにも部分行が残らない
    def test_create_with_credentials_rolls_back_both_rows(self):
        channel = self.make_new_channel()

        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction.atomic():
                self.repository.create_with_credentials(
                    channel,
                    self.make_encrypted_pair(),
                )
                raise RuntimeError("rollback")

        self.assertFalse(LineChannel.objects.filter(public_id=channel.public_id).exists())
        self.assertFalse(LineChannelCredential.objects.exists())

    # テストケース: lock取得後に名称と暗号文ペアだけを更新する
    # 期待値: 公開UUIDと未指定metadataを維持し、両暗号文と更新日時を同時更新する
    def test_update_locked_changes_only_requested_fields_and_complete_pair(self):
        original = self.create_aggregate()
        replacement = self.make_encrypted_pair(b"replacement")

        with transaction.atomic():
            locked = self.repository.get_for_update(original.public_id)
            self.assertIsNotNone(locked)
            updated = self.repository.update_locked(
                locked,
                PersistedChannelMutation(
                    label="更新後の名称",
                    encrypted_credentials=replacement,
                ),
            )

        stored = LineChannel.objects.get(public_id=original.public_id)
        stored_credentials = LineChannelCredential.objects.get(line_channel=stored)
        self.assertEqual(updated.public_id, original.public_id)
        self.assertEqual(stored.messaging_api_channel_id, original.messaging_api_channel_id)
        self.assertEqual(stored.bot_user_id, original.bot_user_id)
        self.assertEqual(stored.label, "更新後の名称")
        self.assertEqual(
            bytes(stored_credentials.access_token_ciphertext),
            replacement.access_token.ciphertext,
        )
        self.assertEqual(
            bytes(stored_credentials.channel_secret_ciphertext),
            replacement.channel_secret.ciphertext,
        )
        self.assertGreater(stored.updated_at, original.updated_at)

    # テストケース: locked更新後にcaller transactionをrollbackする
    # 期待値: metadataと暗号文ペアがともに元の値へ戻る
    def test_update_locked_rolls_back_metadata_and_credentials_together(self):
        original = self.create_aggregate()

        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction.atomic():
                locked = self.repository.get_for_update(original.public_id)
                self.repository.update_locked(
                    locked,
                    PersistedChannelMutation(
                        label="rollback対象",
                        encrypted_credentials=self.make_encrypted_pair(b"rollback"),
                    ),
                )
                raise RuntimeError("rollback")

        stored = LineChannel.objects.get(public_id=original.public_id)
        stored_credentials = LineChannelCredential.objects.get(line_channel=stored)
        self.assertEqual(stored.label, original.label)
        self.assertEqual(
            bytes(stored_credentials.access_token_ciphertext),
            b"initial-access",
        )
        self.assertEqual(
            bytes(stored_credentials.channel_secret_ciphertext),
            b"initial-secret",
        )

    # テストケース: 重複channel IDでaggregateを作成する
    # 期待値: raw DB例外をunique_conflictの安全な永続化エラーへ置換する
    def test_unique_violation_is_classified_without_raw_database_details(self):
        existing = self.create_aggregate()
        duplicate = self.make_new_channel(
            messaging_api_channel_id=existing.messaging_api_channel_id
        )

        with self.assertRaises(PersistenceError) as captured:
            with transaction.atomic():
                self.repository.create_with_credentials(
                    duplicate,
                    self.make_encrypted_pair(),
                )

        self.assertEqual(captured.exception.code, "unique_conflict")
        self.assertEqual(str(captured.exception), "unique_conflict")
        self.assertNotIsInstance(captured.exception, IntegrityError)

    # テストケース: DBがtimeout、deadlock、または一般storage errorを返す
    # 期待値: SQLや値を含めずretryableまたはstorage_unavailableへ分類する
    def test_database_failures_are_safely_classified(self):
        canary = "raw-sql-and-value-canary"
        cases = (
            (OperationalError(1205, canary), "retryable"),
            (OperationalError(1213, canary), "retryable"),
            (DatabaseError(canary), "storage_unavailable"),
        )

        for database_error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with patch.object(
                    QuerySet,
                    "create",
                    side_effect=database_error,
                ):
                    with self.assertRaises(PersistenceError) as captured:
                        with transaction.atomic():
                            self.repository.create_with_credentials(
                                self.make_new_channel(),
                                self.make_encrypted_pair(),
                            )
                self.assertEqual(captured.exception.code, expected_code)
                self.assertNotIn(canary, str(captured.exception))
                self.assertNotIn(canary, repr(captured.exception))

    @staticmethod
    def _summary_for_unsaved(channel):
        from datetime import datetime, timezone

        from linechannels.types import PublicChannelSummary

        now = datetime.now(timezone.utc)
        return PublicChannelSummary(
            public_id=channel.public_id,
            messaging_api_channel_id=channel.messaging_api_channel_id,
            bot_user_id=channel.bot_user_id,
            label=channel.label,
            is_active=channel.is_active,
            credentials_configured=True,
            created_at=now,
            updated_at=now,
        )
