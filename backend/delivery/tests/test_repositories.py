from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from django.test import TestCase

from delivery.models import DeliveryAttempt
from delivery.repositories import (
    DjangoAttemptRepository,
    build_request_fingerprint,
)
from delivery.types import (
    AcceptedDeliveryCommand,
    AttemptAccepted,
    AttemptConflict,
    ExistingAttempt,
    LinkedTargetSnapshot,
    MessageSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
    ReceiptCommitment,
)


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


class RequestFingerprintTests(TestCase):
    def setUp(self):
        self.values = {
            "owner": OwnerPrincipal(1),
            "owner_identity": OwnerIdentitySnapshot(
                UUID("11111111-1111-4111-8111-111111111111")
            ),
            "channel_public_id": UUID(
                "22222222-2222-4222-8222-222222222222"
            ),
            "recipient_public_id": UUID(
                "33333333-3333-4333-8333-333333333333"
            ),
            "message_fingerprint": "4" * 64,
            "receipt_requested": False,
        }

    # テストケース: 同じ型付き入力からrequest fingerprintを繰り返し生成する。
    # 期待値: v1 canonical encodingから常に同じSHA-256値が返る。
    def test_fingerprint_is_stable_for_same_request(self):
        first = build_request_fingerprint(**self.values)
        second = build_request_fingerprint(**self.values)

        self.assertEqual(first, second)
        self.assertEqual(len(first.digest), 64)

    # テストケース: request identityを構成する各軸を一つずつ変更する。
    # 期待値: owner、送信時identity、channel、recipient、message、receipt optionの差を区別する。
    def test_fingerprint_distinguishes_every_request_identity_axis(self):
        baseline = build_request_fingerprint(**self.values)
        variants = (
            {"owner": OwnerPrincipal(2)},
            {"owner_identity": OwnerIdentitySnapshot(uuid4())},
            {"channel_public_id": uuid4()},
            {"recipient_public_id": uuid4()},
            {"message_fingerprint": "5" * 64},
            {"receipt_requested": True},
        )

        for changes in variants:
            with self.subTest(changes=tuple(changes)):
                self.assertNotEqual(
                    baseline,
                    build_request_fingerprint(
                        **(self.values | changes)
                    ),
                )

    # テストケース: receipt要求のcommitment候補だけを変更して同じrequestを表す。
    # 期待値: fingerprint APIはexpiryやdigestを入力に持たず、receipt requested booleanだけで同一になる。
    def test_fingerprint_excludes_receipt_expiry_and_capability_digest(self):
        requested = self.values | {"receipt_requested": True}

        self.assertEqual(
            build_request_fingerprint(**requested),
            build_request_fingerprint(**requested),
        )

    # テストケース: UUID、owner型、strict boolean以外をfingerprint builderへ渡す。
    # 期待値: 暗黙変換せずfield境界で拒否し、曖昧なrequest identityを作らない。
    def test_fingerprint_rejects_untyped_or_coerced_fields(self):
        invalid_changes = (
            {"owner": 1},
            {"owner_identity": self.values["owner_identity"].public_id},
            {"channel_public_id": str(self.values["channel_public_id"])},
            {"recipient_public_id": str(self.values["recipient_public_id"])},
            {"message_fingerprint": "not-a-sha256"},
            {"receipt_requested": 1},
        )

        for changes in invalid_changes:
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(ValueError):
                    build_request_fingerprint(
                        **(self.values | changes)
                    )


class DjangoAttemptRepositoryAcceptTests(TestCase):
    def setUp(self):
        self.repository = DjangoAttemptRepository(clock=lambda: NOW)
        self.owner = OwnerPrincipal(1)
        self.owner_identity = OwnerIdentitySnapshot(
            UUID("11111111-1111-4111-8111-111111111111")
        )
        self.target = LinkedTargetSnapshot(
            channel_public_id=UUID(
                "22222222-2222-4222-8222-222222222222"
            ),
            channel_label="通知チャネル",
            recipient_public_id=UUID(
                "33333333-3333-4333-8333-333333333333"
            ),
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        self.message = MessageSnapshot(
            subject="秘密の件名",
            body="秘密の本文",
            formatted_text="【秘密の件名】\n\n秘密の本文",
            fingerprint="4" * 64,
        )

    def command(
        self,
        *,
        operation_id=None,
        message=None,
        receipt_commitment=None,
    ):
        message = message or self.message
        return AcceptedDeliveryCommand(
            operation_id=operation_id or uuid4(),
            owner=self.owner,
            owner_identity=self.owner_identity,
            target=self.target,
            message=message,
            request_fingerprint=build_request_fingerprint(
                owner=self.owner,
                owner_identity=self.owner_identity,
                channel_public_id=self.target.channel_public_id,
                recipient_public_id=self.target.recipient_public_id,
                message_fingerprint=message.fingerprint,
                receipt_requested=receipt_commitment is not None,
            ),
            receipt_commitment=receipt_commitment,
        )

    # テストケース: linked recipientへの新しい配信要求を受理する。
    # 期待値: processing行と全snapshotを作り、新規受理結果へ同じcanonical operationを返す。
    def test_accept_creates_complete_linked_processing_attempt(self):
        commitment = ReceiptCommitment(
            digest="6" * 64,
            expires_at=NOW + timedelta(hours=24),
        )
        command = self.command(receipt_commitment=commitment)

        result = self.repository.accept(command)

        self.assertIsInstance(result, AttemptAccepted)
        attempt = DeliveryAttempt.objects.get(pk=result.attempt_id)
        self.assertEqual(result.snapshot.operation_id, command.operation_id)
        self.assertEqual(result.snapshot.status, "processing")
        self.assertEqual(result.snapshot.receipt_status, "pending")
        self.assertEqual(attempt.target_mode, "linked_recipient")
        self.assertEqual(attempt.owner_principal_slot, self.owner.slot)
        self.assertEqual(
            attempt.owner_identity_public_id,
            self.owner_identity.public_id,
        )
        self.assertEqual(
            attempt.active_request_fingerprint,
            command.request_fingerprint.digest,
        )
        self.assertIsNone(attempt.active_content_fingerprint)
        self.assertEqual(attempt.accepted_at, NOW)
        self.assertEqual(
            attempt.processing_expires_at,
            NOW + timedelta(seconds=30),
        )
        self.assertEqual(attempt.receipt_token_digest, commitment.digest)
        self.assertEqual(attempt.receipt_expires_at, commitment.expires_at)

    # テストケース: 同じoperation IDと同じrequestを再度受理する。
    # 期待値: 行を追加せず保存済みsnapshotをexistingとして返す。
    def test_same_operation_and_request_returns_existing_attempt(self):
        command = self.command()
        first = self.repository.accept(command)
        second = self.repository.accept(command)

        self.assertIsInstance(first, AttemptAccepted)
        self.assertIsInstance(second, ExistingAttempt)
        self.assertEqual(second.snapshot.operation_id, command.operation_id)
        self.assertEqual(DeliveryAttempt.objects.count(), 1)

    # テストケース: 同じoperation IDを異なるrequest fingerprintで再利用する。
    # 期待値: conflictを返し、既存行と本文を変更しない。
    def test_reused_operation_with_different_request_returns_conflict(self):
        operation_id = uuid4()
        original = self.command(operation_id=operation_id)
        changed = self.command(
            operation_id=operation_id,
            message=MessageSnapshot(
                subject="別件",
                body="別本文",
                formatted_text="【別件】\n\n別本文",
                fingerprint="7" * 64,
            ),
        )
        self.repository.accept(original)

        result = self.repository.accept(changed)

        self.assertIsInstance(result, AttemptConflict)
        attempt = DeliveryAttempt.objects.get(operation_id=operation_id)
        self.assertEqual(attempt.subject, self.message.subject)
        self.assertEqual(DeliveryAttempt.objects.count(), 1)

    # テストケース: 別operation IDで同じactive requestを順次受理する。
    # 期待値: unique競合を回復し、追加行なしで先行canonical operationのexistingを返す。
    def test_different_operation_same_active_request_converges_to_canonical_attempt(
        self,
    ):
        first_commitment = ReceiptCommitment(
            digest="8" * 64,
            expires_at=NOW + timedelta(hours=24),
        )
        losing_commitment = ReceiptCommitment(
            digest="9" * 64,
            expires_at=NOW + timedelta(hours=23),
        )
        first_command = self.command(
            receipt_commitment=first_commitment
        )
        second_command = self.command(
            receipt_commitment=losing_commitment
        )
        first = self.repository.accept(first_command)

        second = self.repository.accept(second_command)

        self.assertIsInstance(first, AttemptAccepted)
        self.assertIsInstance(second, ExistingAttempt)
        self.assertEqual(
            second.snapshot.operation_id,
            first_command.operation_id,
        )
        self.assertNotEqual(
            second.snapshot.operation_id,
            second_command.operation_id,
        )
        self.assertEqual(DeliveryAttempt.objects.count(), 1)
        attempt = DeliveryAttempt.objects.get()
        self.assertEqual(
            attempt.receipt_token_digest,
            first_commitment.digest,
        )
        self.assertNotEqual(
            attempt.receipt_token_digest,
            losing_commitment.digest,
        )

    # テストケース: secretを含むmessageを受理し公開resultを表示する。
    # 期待値: snapshotの表示には件名・本文が現れない。
    def test_accept_result_repr_does_not_expose_message(self):
        result = self.repository.accept(self.command())

        rendered = repr(result.snapshot)
        self.assertNotIn("秘密の件名", rendered)
        self.assertNotIn("秘密の本文", rendered)
