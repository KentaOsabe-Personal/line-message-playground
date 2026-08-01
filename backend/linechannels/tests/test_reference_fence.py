from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, OperationalError, transaction
from django.db.models.query import QuerySet
from django.test import TransactionTestCase

from linechannels.models import LineChannel
from linechannels.reference_fence import DjangoChannelReferenceFence
from linechannels.repositories import RepositoryProgrammingError


class ChannelReferenceFenceTests(TransactionTestCase):
    # テストケース: transaction外またはUUID以外でfenceを取得する
    # 期待値: 公開contract違反としてinsert許可結果を返さない
    def test_lock_requires_caller_transaction_and_canonical_uuid(self):
        fence = DjangoChannelReferenceFence()

        with self.assertRaises(RepositoryProgrammingError):
            fence.lock_existing(uuid4())
        with transaction.atomic(), self.assertRaises(RepositoryProgrammingError):
            fence.lock_existing("not-a-uuid")  # type: ignore[arg-type]

    # テストケース: 存在するチャネルと存在しないチャネルをlockする
    # 期待値: channel rowを取得できた場合だけlockedを返す
    def test_lock_existing_classifies_present_and_missing_channel(self):
        channel = LineChannel.objects.create(
            messaging_api_channel_id="1234567890",
            bot_user_id="U" + "1" * 32,
            label="参照fence",
            provider_id="12345",
            is_active=True,
        )
        fence = DjangoChannelReferenceFence()

        with transaction.atomic():
            locked = fence.lock_existing(channel.public_id)
            missing = fence.lock_existing(uuid4())

        self.assertEqual(locked.status, "locked")
        self.assertEqual(missing.status, "channel_not_found")

    # テストケース: row lock中にdeadlockまたは一般DB失敗が起きる
    # 期待値: 生DB情報を含めずretryableとunavailableを区別する
    def test_lock_storage_failures_are_safely_classified(self):
        fence = DjangoChannelReferenceFence()

        for error, expected in (
            (OperationalError(1213, "secret-canary"), "storage_retryable"),
            (DatabaseError("secret-canary"), "storage_unavailable"),
        ):
            with self.subTest(expected=expected), transaction.atomic(), patch.object(
                QuerySet, "first", side_effect=error
            ):
                result = fence.lock_existing(uuid4())
            self.assertEqual(result.status, expected)
            self.assertNotIn("secret-canary", repr(result))
