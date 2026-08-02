import pickle
from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from linechannels.admin_repositories import DjangoAdminChannelRepository
from linechannels.admin_types import (
    ChannelRevisionProof,
    ChannelSnapshotCommand,
    ExactChannelSnapshotAvailable,
    ExactChannelSnapshotRejected,
    RichMenuChannelSnapshot,
)
from linechannels.crypto import CredentialCryptoError
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.types import AccessToken, EncryptedCredential


class RecordingCipher:
    def __init__(self, *, unreadable=False):
        self.unreadable = unreadable
        self.calls = []

    def decrypt(self, value, context):
        self.calls.append((value.ciphertext, context.channel_public_id, context.kind))
        if self.unreadable:
            raise CredentialCryptoError("credential-unreadable-canary")
        return AccessToken("access-token-canary")


def create_channel(*, provider_id="000123", active=True, credentials=True):
    channel = LineChannel.objects.create(
        messaging_api_channel_id=str(uuid4().int)[:20],
        bot_user_id="U" + uuid4().hex,
        label="リッチメニュー対象",
        provider_id=provider_id,
        is_active=active,
    )
    if credentials:
        LineChannelCredential.objects.create(
            line_channel=channel,
            access_token_ciphertext=b"access-ciphertext",
            channel_secret_ciphertext=b"secret-ciphertext",
        )
    return channel


def snapshot_command(channel, *, provider_id="000123", revision=None):
    return ChannelSnapshotCommand(
        channel_public_id=channel.public_id,
        owner_identity_public_id=uuid4(),
        provider_id=provider_id,
        expected_channel_revision=revision or channel.updated_at,
    )


class ExactProviderSnapshotPortTests(TestCase):
    # テストケース: exact-providerのactive channelから操作snapshotを取得する。
    # 期待値: revision・owner/provider scopeを保持し、tokenは表示・serializeされない。
    def test_snapshot_is_exact_provider_active_and_non_serializable(self):
        cipher = RecordingCipher()
        repository = DjangoAdminChannelRepository(cipher)
        channel = create_channel()
        command = snapshot_command(channel)

        result = repository.snapshot_exact(command)

        self.assertIsInstance(result, ExactChannelSnapshotAvailable)
        snapshot = result.snapshot
        self.assertIsInstance(snapshot, RichMenuChannelSnapshot)
        self.assertEqual(snapshot.channel_public_id, channel.public_id)
        self.assertEqual(snapshot.owner_identity_public_id, command.owner_identity_public_id)
        self.assertEqual(snapshot.provider_id, "000123")
        self.assertEqual(snapshot.channel_revision, channel.updated_at)
        self.assertEqual(snapshot.access_token.reveal_for_use(), "access-token-canary")
        self.assertEqual(
            cipher.calls,
            [(b"access-ciphertext", channel.public_id, "access_token")],
        )
        rendered = repr(snapshot)
        self.assertNotIn("access-token-canary", rendered)
        with self.assertRaises(TypeError):
            pickle.dumps(snapshot)

    # テストケース: provider未設定・不一致、inactive、credential欠落を同じportへ渡す。
    # 期待値: LINE call前に安全な拒否となり、credential復号は一度も行われない。
    def test_provider_scope_is_fail_closed_before_credential_decryption(self):
        cipher = RecordingCipher()
        repository = DjangoAdminChannelRepository(cipher)
        cases = (
            (create_channel(provider_id=None), "channel_unavailable"),
            (create_channel(provider_id="999999"), "channel_unavailable"),
            (create_channel(active=False), "channel_inactive"),
            (create_channel(credentials=False), "credential_unavailable"),
        )

        for channel, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                result = repository.snapshot_exact(snapshot_command(channel))
                self.assertIsInstance(result, ExactChannelSnapshotRejected)
                self.assertEqual(result.code, expected_code)
        self.assertEqual(cipher.calls, [])

    # テストケース: credential decryptが読取不能エラーを返す。
    # 期待値: credential_unreadableへ縮約し、内部エラー文字列を露出しない。
    def test_unreadable_credentials_are_safe_and_never_exposed(self):
        channel = create_channel()
        repository = DjangoAdminChannelRepository(RecordingCipher(unreadable=True))

        result = repository.snapshot_exact(snapshot_command(channel))

        self.assertEqual(result, ExactChannelSnapshotRejected("credential_unreadable"))
        self.assertNotIn("credential-unreadable-canary", repr(result))

    # テストケース: 保存済みchannel revisionより古いrevisionでsnapshotを要求する。
    # 期待値: stale_channelを返し、外部I/O用tokenの復号を開始しない。
    def test_stale_revision_is_rejected_before_credential_decryption(self):
        cipher = RecordingCipher()
        repository = DjangoAdminChannelRepository(cipher)
        channel = create_channel()
        stale_revision = channel.updated_at - timedelta(seconds=1)

        result = repository.snapshot_exact(
            snapshot_command(channel, revision=stale_revision)
        )

        self.assertEqual(result, ExactChannelSnapshotRejected("stale_channel"))
        self.assertEqual(cipher.calls, [])


class ExactProviderRevisionPortTests(TransactionTestCase):
    # テストケース: 外部I/O後のrevision再検証をtransaction内外で実行する。
    # 期待値: transaction必須で、active・provider・revision一致時だけunchangedになる。
    def test_lock_unchanged_requires_atomic_transaction_and_matches_all_axes(self):
        channel = create_channel()
        cipher = RecordingCipher()
        repository = DjangoAdminChannelRepository(cipher)
        snapshot_result = repository.snapshot_exact(snapshot_command(channel))
        self.assertIsInstance(snapshot_result, ExactChannelSnapshotAvailable)
        snapshot = snapshot_result.snapshot
        proof = ChannelRevisionProof.from_snapshot(snapshot)

        with self.assertRaisesRegex(Exception, "transaction_required"):
            repository.lock_unchanged(proof)

        with transaction.atomic():
            unchanged = repository.lock_unchanged(proof)
        self.assertEqual(unchanged.status, "unchanged")

        LineChannel.objects.filter(public_id=channel.public_id).update(is_active=False)
        with transaction.atomic():
            inactive = repository.lock_unchanged(proof)
        self.assertEqual(inactive, ExactChannelSnapshotRejected("channel_inactive"))

    # テストケース: revision proofのprovider不一致と存在しないchannelを検証する。
    # 期待値: 両方をchannel_unavailableへ隠し、対象の存在を推測させない。
    def test_lock_unchanged_hides_provider_mismatch_and_missing_channel(self):
        channel = create_channel()
        repository = DjangoAdminChannelRepository(RecordingCipher())
        snapshot_result = repository.snapshot_exact(snapshot_command(channel))
        self.assertIsInstance(snapshot_result, ExactChannelSnapshotAvailable)
        snapshot = snapshot_result.snapshot

        provider_proof = ChannelRevisionProof(
            owner_identity_public_id=snapshot.owner_identity_public_id,
            provider_id="999999",
            channel_public_id=snapshot.channel_public_id,
            channel_revision=snapshot.channel_revision,
        )
        missing_proof = ChannelRevisionProof(
            owner_identity_public_id=snapshot.owner_identity_public_id,
            provider_id=snapshot.provider_id,
            channel_public_id=uuid4(),
            channel_revision=timezone.now(),
        )

        with transaction.atomic():
            hidden = repository.lock_unchanged(provider_proof)
            absent = repository.lock_unchanged(missing_proof)

        self.assertEqual(hidden, ExactChannelSnapshotRejected("channel_unavailable"))
        self.assertEqual(absent, ExactChannelSnapshotRejected("channel_unavailable"))
