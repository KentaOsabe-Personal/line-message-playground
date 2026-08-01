from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, OperationalError, connection, transaction
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from linechannels.admin_repositories import DjangoAdminChannelRepository
from linechannels.crypto import CredentialCryptoError
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.repositories import RepositoryProgrammingError
from linechannels.types import AccessToken


class RecordingCipher:
    def __init__(self, *, unreadable=False):
        self.values = []
        self.unreadable = unreadable

    def decrypt(self, value, context):
        self.values.append((value.ciphertext, context.kind))
        if self.unreadable:
            raise CredentialCryptoError("credential_unreadable")
        return AccessToken("snapshot-token")


def create_channel(*, provider_id="000123", credentials=True, active=True):
    channel = LineChannel.objects.create(
        messaging_api_channel_id=str(uuid4().int)[:20],
        bot_user_id="U" + uuid4().hex,
        label="管理対象",
        provider_id=provider_id,
        is_active=active,
    )
    credential = None
    if credentials:
        credential = LineChannelCredential.objects.create(
            line_channel=channel,
            access_token_ciphertext=b"access-ciphertext",
            channel_secret_ciphertext=b"secret-ciphertext",
        )
    return channel, credential


class AdminChannelProjectionRepositoryTests(TestCase):
    def setUp(self):
        self.cipher = RecordingCipher()
        self.repository = DjangoAdminChannelRepository(self.cipher)

    # テストケース: 同一provider、legacy、別provider、資格情報欠損を一覧投影する
    # 期待値: 同一providerとlegacyだけを1 queryで全非秘密field・状態付きで返す
    def test_list_projects_safe_fields_without_materializing_ciphertext(self):
        same, same_credential = create_channel(active=False)
        legacy, _ = create_channel(provider_id=None)
        create_channel(provider_id="999999")
        broken, _ = create_channel(credentials=False)

        with patch.object(
            LineChannelCredential,
            "from_db",
            side_effect=AssertionError("credential row must not materialize"),
        ), CaptureQueriesContext(connection) as queries:
            result = self.repository.list_for_owner_provider("000123")

        self.assertEqual(len(queries), 1)
        self.assertEqual(
            {item.public_id for item in result},
            {same.public_id, legacy.public_id, broken.public_id},
        )
        by_id = {item.public_id: item for item in result}
        projected = by_id[same.public_id]
        self.assertEqual(projected.messaging_api_channel_id, same.messaging_api_channel_id)
        self.assertEqual(projected.bot_user_id, same.bot_user_id)
        self.assertEqual(projected.label, same.label)
        self.assertEqual(projected.provider_id, "000123")
        self.assertFalse(projected.is_active)
        self.assertEqual(projected.credentials_state, "configured")
        self.assertEqual(
            projected.credentials_updated_at, same_credential.updated_at
        )
        self.assertEqual(projected.created_at, same.created_at)
        self.assertEqual(projected.updated_at, same.updated_at)
        self.assertEqual(by_id[broken.public_id].credentials_state, "repair_required")
        self.assertIsNone(by_id[broken.public_id].credentials_updated_at)
        self.assertEqual(self.cipher.values, [])

    # テストケース: 制約外データとして片側暗号文が空のcredential行を投影する
    # 期待値: 暗号文自体を取得せずrepair_requiredとcredential更新日時を返す
    def test_partial_credential_pair_is_repair_required(self):
        channel, credential = create_channel()
        item = self.repository._view(
            {
                "public_id": channel.public_id,
                "messaging_api_channel_id": channel.messaging_api_channel_id,
                "bot_user_id": channel.bot_user_id,
                "label": channel.label,
                "provider_id": channel.provider_id,
                "is_active": channel.is_active,
                "created_at": channel.created_at,
                "updated_at": channel.updated_at,
                "admin_credentials_configured": True,
                "admin_credentials_complete": False,
                "admin_credentials_updated_at": credential.updated_at,
            }
        )

        self.assertEqual(item.credentials_state, "repair_required")
        self.assertEqual(item.credentials_updated_at, credential.updated_at)
        self.assertEqual(self.cipher.values, [])

    # テストケース: same-providerとlegacyの詳細、別provider、不在、削除済みを取得する
    # 期待値: 対象範囲だけsafe detailを返し、他は他チャネルを代替表示せずNoneになる
    def test_detail_scope_not_found_and_deleted_are_safe(self):
        same, _ = create_channel()
        legacy, _ = create_channel(provider_id=None)
        other, _ = create_channel(provider_id="999999")
        deleted, deleted_credential = create_channel()
        deleted_id = deleted.public_id
        deleted_credential.delete()
        deleted.delete()

        self.assertEqual(
            self.repository.get_for_owner_provider(same.public_id, "000123").public_id,
            same.public_id,
        )
        self.assertEqual(
            self.repository.get_for_owner_provider(legacy.public_id, "000123").public_id,
            legacy.public_id,
        )
        self.assertIsNone(
            self.repository.get_for_owner_provider(other.public_id, "000123")
        )
        self.assertIsNone(self.repository.get_for_owner_provider(uuid4(), "000123"))
        self.assertIsNone(
            self.repository.get_for_owner_provider(deleted_id, "000123")
        )


class AdminConnectionSnapshotRepositoryTests(TestCase):
    def setUp(self):
        self.cipher = RecordingCipher()
        self.repository = DjangoAdminChannelRepository(self.cipher)

    # テストケース: inactiveチャネルの接続確認snapshotを取得する
    # 期待値: token、bot ID、aware revisionを単一queryで取得しaccess tokenだけ復号する
    def test_snapshot_is_single_query_inactive_safe_and_access_token_only(self):
        channel, _ = create_channel(active=False)

        with CaptureQueriesContext(connection) as queries:
            snapshot = self.repository.get_connection_snapshot(
                channel.public_id, "000123"
            )

        channel.refresh_from_db()
        self.assertEqual(len(queries), 1)
        sql = queries[0]["sql"].lower()
        self.assertIn("access_token_ciphertext", sql)
        self.assertNotIn("channel_secret_ciphertext", sql)
        self.assertEqual(snapshot.status, "available")
        self.assertEqual(snapshot.expected_bot_user_id, channel.bot_user_id)
        self.assertEqual(snapshot.expected_updated_at, channel.updated_at)
        self.assertIsNotNone(snapshot.expected_updated_at.tzinfo)
        self.assertEqual(self.cipher.values, [(b"access-ciphertext", "access_token")])
        self.assertNotIn("snapshot-token", repr(snapshot))

    # テストケース: 資格情報欠損・破損、別provider、不在のsnapshotを取得する
    # 期待値: tokenや他チャネル情報を返さず固定safe分類へ収束する
    def test_missing_corrupt_other_provider_and_absent_are_safe(self):
        missing, _ = create_channel(credentials=False)
        corrupt, _ = create_channel()
        other, _ = create_channel(provider_id="999999")

        missing_result = self.repository.get_connection_snapshot(
            missing.public_id, "000123"
        )
        corrupt_result = DjangoAdminChannelRepository(
            RecordingCipher(unreadable=True)
        ).get_connection_snapshot(corrupt.public_id, "000123")
        other_result = self.repository.get_connection_snapshot(other.public_id, "000123")
        absent = self.repository.get_connection_snapshot(uuid4(), "000123")

        self.assertEqual(missing_result.code, "credential_unavailable")
        self.assertEqual(corrupt_result.code, "credential_unreadable")
        self.assertEqual(other_result.code, "channel_not_found")
        self.assertEqual(absent.code, "channel_not_found")

    # テストケース: snapshot queryがdeadlockまたは一般DB失敗を返す
    # 期待値: 生DB情報なしでretryable/unavailableを区別する
    def test_snapshot_storage_failures_are_safely_classified(self):
        for error, expected in (
            (OperationalError(1205, "raw-canary"), "storage_retryable"),
            (DatabaseError("raw-canary"), "storage_unavailable"),
        ):
            with self.subTest(expected=expected), patch.object(
                QuerySet, "first", side_effect=error
            ):
                result = self.repository.get_connection_snapshot(uuid4(), "000123")
            self.assertEqual(result.code, expected)
            self.assertNotIn("raw-canary", repr(result))


class AdminConnectionRevisionRepositoryTests(TransactionTestCase):
    # テストケース: transaction外、naive revision、別provider、不在でrevision lockを要求する
    # 期待値: programming errorまたはchannel_not_foundとなり他チャネルを開示しない
    def test_revision_lock_requires_transaction_aware_time_and_provider_scope(self):
        channel, _ = create_channel()
        other, _ = create_channel(provider_id="999999")
        repository = DjangoAdminChannelRepository(RecordingCipher())

        with self.assertRaises(RepositoryProgrammingError):
            repository.lock_connection_revision(
                channel.public_id, "000123", channel.updated_at
            )
        with transaction.atomic(), self.assertRaises(RepositoryProgrammingError):
            repository.lock_connection_revision(
                channel.public_id, "000123", datetime.now()
            )
        with transaction.atomic():
            hidden = repository.lock_connection_revision(
                other.public_id, "000123", other.updated_at
            )
            absent = repository.lock_connection_revision(
                uuid4(), "000123", timezone.now()
            )
        self.assertEqual(hidden.code, "channel_not_found")
        self.assertEqual(absent.code, "channel_not_found")

    # テストケース: DB round-trip revisionの一致後にチャネル更新を挟んで再検証する
    # 期待値: 完全一致はunchanged、更新後はstale_channelになる
    def test_revision_lock_uses_exact_database_round_trip_value(self):
        channel, _ = create_channel()
        repository = DjangoAdminChannelRepository(RecordingCipher())
        channel.refresh_from_db()

        with transaction.atomic():
            unchanged = repository.lock_connection_revision(
                channel.public_id, "000123", channel.updated_at
            )
        LineChannel.objects.filter(pk=channel.pk).update(
            updated_at=channel.updated_at + timedelta(seconds=1)
        )
        with transaction.atomic():
            stale = repository.lock_connection_revision(
                channel.public_id, "000123", channel.updated_at
            )

        self.assertEqual(unchanged.status, "unchanged")
        self.assertEqual(stale.code, "stale_channel")

    # テストケース: revision row lock queryがdeadlockまたは一般DB失敗を返す
    # 期待値: retryableとstorage_unavailableを固定safe分類で返す
    def test_revision_lock_storage_failures_are_safely_classified(self):
        repository = DjangoAdminChannelRepository(RecordingCipher())
        for error, expected in (
            (OperationalError(1213, "raw-canary"), "storage_retryable"),
            (DatabaseError("raw-canary"), "storage_unavailable"),
        ):
            with self.subTest(expected=expected), transaction.atomic(), patch.object(
                QuerySet, "first", side_effect=error
            ):
                result = repository.lock_connection_revision(
                    uuid4(), "000123", timezone.now()
                )
            self.assertEqual(result.code, expected)
            self.assertNotIn("raw-canary", repr(result))
