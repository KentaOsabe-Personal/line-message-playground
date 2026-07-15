import uuid
from contextlib import contextmanager

from django.db import transaction
from django.test import TransactionTestCase

from linechannels.models import LineChannel
from linechannels.repositories import PersistenceError
from linechannels.rotation import (
    CredentialRotationService,
    DefaultCredentialRotationService,
)
from linechannels.rotation_item import (
    PrimaryVerificationFailed,
    PrimaryVerificationVerified,
    RotationItemFailed,
    RotationItemRotated,
    RotationItemVerified,
)
from linechannels.types import EncryptedCredential, EncryptedCredentialPair


class CredentialRotationServiceTests(TransactionTestCase):
    def setUp(self):
        self.first_id = uuid.UUID(int=1)
        self.second_id = uuid.UUID(int=2)
        self.pair = EncryptedCredentialPair(
            EncryptedCredential(b"access"), EncryptedCredential(b"secret")
        )

    def service(self, *, readiness="ready", acquired=True, repository=None, processor=None):
        cipher = FakeReadinessCipher(readiness)
        lock = FakeRotationLock(acquired)
        repository = repository or FakeRotationRepository(
            (self.first_id,), {self.first_id: self.pair}
        )
        processor = processor or FakeProcessor()
        service = DefaultCredentialRotationService(
            cipher, repository, lock, processor
        )
        return service, cipher, repository, lock, processor

    # テストケース: 旧鍵がない単一鍵構成でrotationを要求する
    # 期待値: repository・DB transaction・advisory lockへ触れず変更ゼロのconfiguration_requiredを返す
    def test_old_key_missing_returns_configuration_required_before_dependencies(self):
        service, cipher, repository, lock, processor = self.service(
            readiness="old_key_missing"
        )

        summary = service.rotate_all()

        self.assertEqual(summary.status, "configuration_required")
        self.assertEqual(summary.verified_count, 0)
        self.assertEqual(summary.rotated_count, 0)
        self.assertEqual(summary.failed_count, 0)
        self.assertEqual(summary.failures, ())
        self.assertFalse(summary.old_keys_removable)
        self.assertEqual(lock.acquire_calls, 0)
        self.assertEqual(repository.list_calls, 0)
        self.assertEqual(processor.process_calls, [])

    # テストケース: 別commandがbatch lockを保持している
    # 期待値: snapshot走査や行変更をせずbusyとして返す
    def test_busy_returns_without_scanning_rows(self):
        service, _, repository, lock, processor = self.service(acquired=False)

        summary = service.rotate_all()

        self.assertEqual(summary.status, "busy")
        self.assertFalse(summary.old_keys_removable)
        self.assertEqual(repository.list_calls, 0)
        self.assertEqual(processor.process_calls, [])
        self.assertTrue(lock.released)

    # テストケース: snapshotにprimary済み行と旧鍵行があり、final sweepも全件成功する
    # 期待値: verifiedは無変更、rotated pairだけ単一更新し、fresh snapshot後にcompleteを返す
    def test_rotates_each_row_then_completes_only_after_fresh_primary_sweep(self):
        rotated_pair = EncryptedCredentialPair(
            EncryptedCredential(b"new-access"), EncryptedCredential(b"new-secret")
        )
        repository = FakeRotationRepository(
            (self.first_id, self.second_id),
            {self.first_id: self.pair, self.second_id: self.pair},
        )
        processor = FakeProcessor(
            process_results={
                self.first_id: RotationItemVerified(),
                self.second_id: RotationItemRotated(rotated_pair),
            }
        )
        service, _, repository, lock, processor = self.service(
            repository=repository, processor=processor
        )

        summary = service.rotate_all()

        self.assertEqual(summary.status, "complete")
        self.assertEqual(summary.verified_count, 1)
        self.assertEqual(summary.rotated_count, 1)
        self.assertEqual(summary.failed_count, 0)
        self.assertTrue(summary.old_keys_removable)
        self.assertEqual(repository.replacements, [(self.second_id, rotated_pair)])
        self.assertEqual(repository.list_calls, 2)
        self.assertEqual(processor.verify_calls, [self.first_id, self.second_id])
        self.assertTrue(lock.released)

    # テストケース: 初回走査後にfresh snapshotへ新しい未検証行が現れる
    # 期待値: primary-only final sweepの失敗を公開UUIDとsafe codeで集計しcompleteにしない
    def test_fresh_sweep_detects_new_or_failed_rows_and_reports_incomplete(self):
        repository = FakeRotationRepository(
            (self.first_id,),
            {self.first_id: self.pair, self.second_id: self.pair},
            snapshots=(
                (self.first_id,),
                (self.first_id, self.second_id),
            ),
        )
        processor = FakeProcessor(
            verify_results={
                self.second_id: PrimaryVerificationFailed("credential_unreadable")
            }
        )
        service, _, _, _, _ = self.service(
            repository=repository, processor=processor
        )

        summary = service.rotate_all()

        self.assertEqual(summary.status, "incomplete")
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(summary.failures[0].channel_public_id, self.second_id)
        self.assertEqual(summary.failures[0].code, "credential_unreadable")
        self.assertFalse(summary.old_keys_removable)
        self.assertEqual(repository.list_calls, 2)

    # テストケース: 行処理で暗号失敗・欠損・deadlockが発生する
    # 期待値: 他行を継続し、公開UUIDと安全なcodeだけを一度ずつ集計する
    def test_row_failures_are_isolated_and_safely_aggregated(self):
        third_id = uuid.UUID(int=3)
        repository = FakeRotationRepository(
            (self.first_id, self.second_id, third_id),
            {self.first_id: self.pair, third_id: self.pair},
            get_errors={third_id: PersistenceError("retryable")},
        )
        processor = FakeProcessor(
            process_results={
                self.first_id: RotationItemFailed("credential_unreadable")
            },
            verify_results={
                self.first_id: PrimaryVerificationFailed("credential_unreadable"),
                self.second_id: PrimaryVerificationFailed("credential_unreadable"),
                third_id: PrimaryVerificationFailed("credential_unreadable"),
            },
        )
        service, _, _, _, _ = self.service(
            repository=repository, processor=processor
        )

        summary = service.rotate_all()

        self.assertEqual(summary.status, "incomplete")
        self.assertEqual(summary.failed_count, 3)
        self.assertEqual(
            [(failure.channel_public_id, failure.code) for failure in summary.failures],
            [
                (self.first_id, "credential_unreadable"),
                (self.second_id, "credential_missing"),
                (third_id, "retryable"),
            ],
        )
        self.assertFalse(summary.old_keys_removable)

    # テストケース: 再実行時に全行がprimary済みとして判定される
    # 期待値: 暗号文更新を一度も行わずverified件数だけを増やして収束する
    def test_rerun_keeps_primary_verified_rows_unchanged(self):
        service, _, repository, _, _ = self.service()

        summary = service.rotate_all()

        self.assertEqual(summary.status, "complete")
        self.assertEqual(summary.verified_count, 1)
        self.assertEqual(summary.rotated_count, 0)
        self.assertEqual(repository.replacements, [])

    # テストケース: pair更新中にKeyboardInterruptが発生する
    # 期待値: 処理中1行のtransactionだけをrollbackし、batch lockを明示解放して割込みを伝播する
    def test_interrupt_rolls_back_current_row_and_releases_batch_lock(self):
        repository = InterruptingRotationRepository(
            (self.first_id,), {self.first_id: self.pair}
        )
        processor = FakeProcessor(
            process_results={self.first_id: RotationItemRotated(self.pair)}
        )
        service, _, _, lock, _ = self.service(
            repository=repository, processor=processor
        )

        with self.assertRaises(KeyboardInterrupt):
            service.rotate_all()

        self.assertFalse(LineChannel.objects.filter(public_id=self.first_id).exists())
        self.assertTrue(lock.released)

    # テストケース: 具象serviceを全件rotation公開contractとして扱う
    # 期待値: CredentialRotationService Protocolへ構造的に適合する
    def test_concrete_service_implements_public_protocol(self):
        service, *_ = self.service()
        self.assertIsInstance(service, CredentialRotationService)


class FakeReadinessCipher:
    def __init__(self, readiness):
        self.readiness = readiness
        self.calls = 0

    def rotation_readiness(self):
        self.calls += 1
        return self.readiness


class FakeRotationLock:
    def __init__(self, acquired):
        self.acquired = acquired
        self.acquire_calls = 0
        self.released = False

    @contextmanager
    def acquire(self):
        self.acquire_calls += 1
        try:
            yield self.acquired
        finally:
            self.released = True


class FakeRotationRepository:
    def __init__(self, snapshot, pairs, *, snapshots=None, get_errors=None):
        self.snapshot = snapshot
        self.snapshots = snapshots
        self.pairs = pairs
        self.get_errors = get_errors or {}
        self.list_calls = 0
        self.replacements = []

    def list_credential_public_ids(self):
        index = self.list_calls
        self.list_calls += 1
        if self.snapshots is not None:
            return self.snapshots[min(index, len(self.snapshots) - 1)]
        return self.snapshot

    def get_credentials_for_update(self, public_id):
        error = self.get_errors.get(public_id)
        if error is not None:
            raise error
        return self.pairs.get(public_id)

    def replace_credentials_locked(self, public_id, credentials):
        self.replacements.append((public_id, credentials))
        self.pairs[public_id] = credentials


class InterruptingRotationRepository(FakeRotationRepository):
    def replace_credentials_locked(self, public_id, credentials):
        LineChannel.objects.create(
            public_id=public_id,
            messaging_api_channel_id="999999999",
            bot_user_id="U" + "9" * 32,
            label="rollback-canary",
            is_active=True,
        )
        raise KeyboardInterrupt()


class FakeProcessor:
    def __init__(self, *, process_results=None, verify_results=None):
        self.process_results = process_results or {}
        self.verify_results = verify_results or {}
        self.process_calls = []
        self.verify_calls = []

    def process(self, public_id, credentials):
        self.process_calls.append(public_id)
        return self.process_results.get(public_id, RotationItemVerified())

    def verify_with_primary(self, public_id, credentials):
        self.verify_calls.append(public_id)
        return self.verify_results.get(public_id, PrimaryVerificationVerified())
