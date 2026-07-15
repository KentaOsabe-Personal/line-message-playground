import uuid
from unittest.mock import patch

from django.db import DatabaseError, OperationalError, transaction
from django.db.models.query import QuerySet
from django.test import TransactionTestCase

from linechannels.models import LineChannel, LineChannelCredential
from linechannels.repositories import PersistenceError, RepositoryProgrammingError
from linechannels.rotation_repository import (
    DjangoRotationCredentialRepository,
    RotationCredentialRepository,
)
from linechannels.types import EncryptedCredential, EncryptedCredentialPair


class DjangoRotationCredentialRepositoryTests(TransactionTestCase):
    def setUp(self):
        self.repository = DjangoRotationCredentialRepository()

    def create_credential(self, public_id=None, marker=b"initial"):
        suffix = uuid.uuid4().hex
        channel = LineChannel.objects.create(
            public_id=public_id or uuid.uuid4(),
            messaging_api_channel_id=str(int(suffix[:12], 16)),
            bot_user_id=f"U{suffix}",
            label="rotation検証用",
            is_active=True,
        )
        LineChannelCredential.objects.create(
            line_channel=channel,
            access_token_ciphertext=marker + b"-access",
            channel_secret_ciphertext=marker + b"-secret",
        )
        return channel

    @staticmethod
    def pair(marker=b"replacement"):
        return EncryptedCredentialPair(
            EncryptedCredential(marker + b"-access"),
            EncryptedCredential(marker + b"-secret"),
        )

    # テストケース: 資格情報を持つチャネルをUUID順不同で保存してsnapshotを取得する
    # 期待値: public UUID昇順のimmutable tupleだけが返り、資格情報欠損行は含まれない
    def test_lists_stable_sorted_credential_snapshot(self):
        public_ids = [uuid.UUID(int=3), uuid.UUID(int=1), uuid.UUID(int=2)]
        for public_id in public_ids:
            self.create_credential(public_id)
        missing = self.create_credential(uuid.UUID(int=4))
        missing.credential.delete()

        snapshot = self.repository.list_credential_public_ids()

        self.assertEqual(snapshot, tuple(sorted(public_ids)))
        self.assertIsInstance(snapshot, tuple)

    # テストケース: rotation用locked操作をcaller transaction外で呼び出す
    # 期待値: DB変更前にtransaction_requiredとして安全に拒否される
    def test_locked_operations_require_caller_owned_transaction(self):
        channel = self.create_credential()

        for operation in (
            lambda: self.repository.get_credentials_for_update(channel.public_id),
            lambda: self.repository.replace_credentials_locked(
                channel.public_id, self.pair()
            ),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(RepositoryProgrammingError) as captured:
                    operation()
                self.assertEqual(captured.exception.code, "transaction_required")

    # テストケース: 行transaction内で資格情報をlock後に完全pairで置換する
    # 期待値: 2暗号文が同時commitされ、caller rollback時は元pairが保持される
    def test_locked_pair_update_commits_and_rolls_back_atomically(self):
        committed = self.create_credential()
        rolled_back = self.create_credential(marker=b"original")

        with transaction.atomic():
            current = self.repository.get_credentials_for_update(committed.public_id)
            self.assertEqual(current.access_token.ciphertext, b"initial-access")
            self.repository.replace_credentials_locked(
                committed.public_id, self.pair(b"committed")
            )

        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction.atomic():
                self.repository.get_credentials_for_update(rolled_back.public_id)
                self.repository.replace_credentials_locked(
                    rolled_back.public_id, self.pair(b"rolled-back")
                )
                raise RuntimeError("rollback")

        committed_value = LineChannelCredential.objects.get(line_channel=committed)
        rolled_back_value = LineChannelCredential.objects.get(line_channel=rolled_back)
        self.assertEqual(bytes(committed_value.access_token_ciphertext), b"committed-access")
        self.assertEqual(bytes(committed_value.channel_secret_ciphertext), b"committed-secret")
        self.assertEqual(bytes(rolled_back_value.access_token_ciphertext), b"original-access")
        self.assertEqual(bytes(rolled_back_value.channel_secret_ciphertext), b"original-secret")

    # テストケース: lock対象行の消失とdeadlock/storage failureが発生する
    # 期待値: SQLや値を含まない安全な分類へ置換され、暗号文は変更されない
    def test_missing_and_database_failures_are_safely_classified(self):
        missing_id = uuid.uuid4()
        with transaction.atomic():
            self.assertIsNone(self.repository.get_credentials_for_update(missing_id))
            with self.assertRaises(PersistenceError) as captured:
                self.repository.replace_credentials_locked(missing_id, self.pair())
        self.assertEqual(captured.exception.code, "credentials_incomplete")

        canary = "raw-sql-ciphertext-canary"
        cases = (
            (OperationalError(1213, canary), "retryable"),
            (DatabaseError(canary), "storage_unavailable"),
        )
        for database_error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with patch.object(QuerySet, "values_list", side_effect=database_error):
                    with self.assertRaises(PersistenceError) as captured:
                        self.repository.list_credential_public_ids()
                self.assertEqual(captured.exception.code, expected_code)
                self.assertNotIn(canary, str(captured.exception))

    # テストケース: Django具象repositoryをrotation専用公開contractとして扱う
    # 期待値: RotationCredentialRepository Protocolへ構造的に適合する
    def test_concrete_repository_implements_public_protocol(self):
        self.assertIsInstance(self.repository, RotationCredentialRepository)
