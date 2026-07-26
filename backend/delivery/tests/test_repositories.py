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
    ConfirmReceiptCommand,
    ExistingAttempt,
    LinkedTargetSnapshot,
    LinePushAccepted,
    LinePushRejected,
    LinePushUnknown,
    MessageSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
    ReceiptCommitment,
    ReceiptRecorded,
    ReceiptRejected,
    ReceiptUnchanged,
)
from lineaccounts.models import LineIdentity, OwnerAccount


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


class DjangoAttemptRepositoryFinalizeAndLookupTests(TestCase):
    def setUp(self):
        self.clock_now = NOW
        self.repository = DjangoAttemptRepository(
            clock=lambda: self.clock_now
        )
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
            subject="件名",
            body="本文",
            formatted_text="【件名】\n\n本文",
            fingerprint="4" * 64,
        )

    def accept(self, *, identity=None, receipt=False):
        identity = identity or self.owner_identity
        commitment = (
            ReceiptCommitment(
                digest="6" * 64,
                expires_at=NOW + timedelta(hours=24),
            )
            if receipt
            else None
        )
        operation_id = uuid4()
        command = AcceptedDeliveryCommand(
            operation_id=operation_id,
            owner=self.owner,
            owner_identity=identity,
            target=self.target,
            message=self.message,
            request_fingerprint=build_request_fingerprint(
                owner=self.owner,
                owner_identity=identity,
                channel_public_id=self.target.channel_public_id,
                recipient_public_id=self.target.recipient_public_id,
                message_fingerprint=self.message.fingerprint,
                receipt_requested=receipt,
            ),
            receipt_commitment=commitment,
        )
        result = self.repository.accept(command)
        self.assertIsInstance(result, AttemptAccepted)
        return result

    # テストケース: LINE受付結果でprocessing attemptを確定する。
    # 期待値: succeededと両request ID、送信・完了日時を保存し、active fingerprintを解放する。
    def test_finalize_accepted_records_ids_and_releases_active_request(self):
        accepted = self.accept(receipt=True)
        completed_at = NOW + timedelta(seconds=2)

        snapshot = self.repository.finalize(
            accepted.attempt_id,
            LinePushAccepted("line-request", "accepted-request"),
            completed_at,
        )

        attempt = DeliveryAttempt.objects.get(pk=accepted.attempt_id)
        self.assertEqual(snapshot.status, "succeeded")
        self.assertEqual(snapshot.line_request_id, "line-request")
        self.assertEqual(
            snapshot.line_accepted_request_id,
            "accepted-request",
        )
        self.assertIsNone(snapshot.failure)
        self.assertEqual(snapshot.completed_at, completed_at)
        self.assertEqual(attempt.sent_at, completed_at)
        self.assertIsNone(attempt.failed_at)
        self.assertIsNone(attempt.active_request_fingerprint)
        self.assertEqual(attempt.receipt_token_digest, "6" * 64)
        self.assertEqual(snapshot.receipt_status, "pending")

    # テストケース: LINEの明示拒否分類をそれぞれ確定する。
    # 期待値: failedへ移し、受け取った安全な分類を変換せず保存する。
    def test_finalize_rejected_preserves_precise_failure_type(self):
        for index, failure_type in enumerate(
            (
                "invalid_request",
                "authentication",
                "permission",
                "conflict",
                "rate_limited",
            )
        ):
            with self.subTest(failure_type=failure_type):
                accepted = self.accept(
                    identity=OwnerIdentitySnapshot(uuid4())
                )
                completed_at = NOW + timedelta(seconds=index + 1)

                snapshot = self.repository.finalize(
                    accepted.attempt_id,
                    LinePushRejected(failure_type),
                    completed_at,
                )

                attempt = DeliveryAttempt.objects.get(
                    pk=accepted.attempt_id
                )
                self.assertEqual(snapshot.status, "failed")
                self.assertEqual(snapshot.failure, failure_type)
                self.assertEqual(attempt.failed_at, completed_at)
                self.assertIsNone(attempt.sent_at)
                self.assertIsNone(
                    attempt.active_request_fingerprint
                )

    # テストケース: LINEとの通信結果不明を確定する。
    # 期待値: unknownとして安全な不明分類を保持し、成功とは推測しない。
    def test_finalize_unknown_preserves_unknown_failure_type(self):
        accepted = self.accept()
        completed_at = NOW + timedelta(seconds=2)

        snapshot = self.repository.finalize(
            accepted.attempt_id,
            LinePushUnknown("response_unknown"),
            completed_at,
        )

        self.assertEqual(snapshot.status, "unknown")
        self.assertEqual(snapshot.failure, "response_unknown")
        self.assertEqual(snapshot.completed_at, completed_at)

    # テストケース: 同じattemptへ異なる終端結果を順に確定する。
    # 期待値: processing条件の先行結果だけが勝ち、後続結果と日時で上書きしない。
    def test_finalize_keeps_first_terminal_result(self):
        accepted = self.accept()
        first_at = NOW + timedelta(seconds=1)
        later_at = NOW + timedelta(seconds=2)
        first = self.repository.finalize(
            accepted.attempt_id,
            LinePushUnknown("timeout_unknown"),
            first_at,
        )

        second = self.repository.finalize(
            accepted.attempt_id,
            LinePushAccepted("late-request", None),
            later_at,
        )

        self.assertEqual(first.status, "unknown")
        self.assertEqual(second.status, "unknown")
        self.assertEqual(second.failure, "timeout_unknown")
        self.assertEqual(second.completed_at, first_at)
        self.assertIsNone(second.line_request_id)

    # テストケース: operation IDをowner principal slotと同時に照会する。
    # 期待値: 正しいownerだけにsnapshotを返し、送信時identity UUIDは認可キーにしない。
    def test_get_for_owner_scopes_by_principal_not_identity_snapshot(self):
        historical_identity = OwnerIdentitySnapshot(uuid4())
        accepted = self.accept(identity=historical_identity)

        visible = self.repository.get_for_owner(
            self.owner.slot,
            accepted.snapshot.operation_id,
        )
        hidden = self.repository.get_for_owner(
            self.owner.slot + 1,
            accepted.snapshot.operation_id,
        )

        self.assertIsNotNone(visible)
        self.assertEqual(
            visible.owner_identity,
            historical_identity,
        )
        self.assertIsNone(hidden)

    # テストケース: processing期限の直前・境界・直後にowner照会する。
    # 期待値: 直前だけprocessingで、境界以降は一度だけprocessing_expiredのunknownへ確定する。
    def test_get_for_owner_expires_processing_at_inclusive_boundary(self):
        for offset, expected_status in (
            (timedelta(microseconds=-1), "processing"),
            (timedelta(), "unknown"),
            (timedelta(microseconds=1), "unknown"),
        ):
            with self.subTest(offset=offset):
                self.clock_now = NOW
                accepted = self.accept(
                    identity=OwnerIdentitySnapshot(uuid4())
                )
                self.clock_now = NOW + timedelta(seconds=30) + offset

                first = self.repository.get_for_owner(
                    self.owner.slot,
                    accepted.snapshot.operation_id,
                )
                second = self.repository.get_for_owner(
                    self.owner.slot,
                    accepted.snapshot.operation_id,
                )

                self.assertEqual(first.status, expected_status)
                self.assertEqual(second.status, expected_status)
                if expected_status == "unknown":
                    self.assertEqual(
                        first.failure,
                        "processing_expired",
                    )
                    self.assertEqual(first.completed_at, self.clock_now)
                    self.assertEqual(
                        second.completed_at,
                        self.clock_now,
                    )
                else:
                    self.assertIsNone(first.failure)

    # テストケース: 別ownerが期限切れprocessing operationを照会する。
    # 期待値: 存在を隠すだけで、他ownerによる照会を契機にattemptを更新しない。
    def test_wrong_owner_lookup_does_not_expire_attempt(self):
        accepted = self.accept()
        self.clock_now = NOW + timedelta(seconds=31)

        result = self.repository.get_for_owner(
            self.owner.slot + 1,
            accepted.snapshot.operation_id,
        )

        self.assertIsNone(result)
        attempt = DeliveryAttempt.objects.get(pk=accepted.attempt_id)
        self.assertEqual(attempt.status, "processing")

    # テストケース: migration済みのfixed配信に旧service failure分類が残っている。
    # 期待値: 分類を別値へ読み替えず、owner status snapshotとしてそのまま参照できる。
    def test_get_for_owner_preserves_legacy_fixed_failure_snapshot(self):
        operation_id = uuid4()
        completed_at = NOW - timedelta(days=1)
        DeliveryAttempt.objects.create(
            operation_id=operation_id,
            subject="旧件名",
            body="旧本文",
            formatted_text="【旧件名】\n\n旧本文",
            content_fingerprint="a" * 64,
            active_content_fingerprint=None,
            request_fingerprint="a" * 64,
            active_request_fingerprint=None,
            target_mode=DeliveryAttempt.TargetMode.FIXED_USER,
            owner_principal_slot=self.owner.slot,
            owner_identity_public_id=None,
            status=DeliveryAttempt.Status.FAILED,
            failure_type=(
                DeliveryAttempt.FailureType.SERVICE_UNAVAILABLE
            ),
            accepted_at=completed_at - timedelta(seconds=1),
            processing_expires_at=completed_at,
            failed_at=completed_at,
            completed_at=completed_at,
        )

        snapshot = self.repository.get_for_owner(
            self.owner.slot,
            operation_id,
        )

        self.assertEqual(snapshot.target.mode, "fixed_user")
        self.assertIsNone(snapshot.owner_identity)
        self.assertEqual(snapshot.status, "failed")
        self.assertEqual(snapshot.failure, "service_unavailable")


class DjangoAttemptRepositoryReceiptTests(TestCase):
    def setUp(self):
        self.repository = DjangoAttemptRepository(clock=lambda: NOW)
        self.channel_public_id = UUID(
            "22222222-2222-4222-8222-222222222222"
        )
        self.recipient_public_id = UUID(
            "33333333-3333-4333-8333-333333333333"
        )
        self.digest = "6" * 64
        self.expiry = NOW + timedelta(hours=24)

    def accept(self, *, receipt=True):
        owner = OwnerPrincipal(1)
        identity = OwnerIdentitySnapshot(uuid4())
        target = LinkedTargetSnapshot(
            channel_public_id=self.channel_public_id,
            channel_label="通知チャネル",
            recipient_public_id=self.recipient_public_id,
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        message = MessageSnapshot(
            subject="件名",
            body="本文",
            formatted_text="【件名】\n\n本文",
            fingerprint="4" * 64,
        )
        command = AcceptedDeliveryCommand(
            operation_id=uuid4(),
            owner=owner,
            owner_identity=identity,
            target=target,
            message=message,
            request_fingerprint=build_request_fingerprint(
                owner=owner,
                owner_identity=identity,
                channel_public_id=target.channel_public_id,
                recipient_public_id=target.recipient_public_id,
                message_fingerprint=message.fingerprint,
                receipt_requested=receipt,
            ),
            receipt_commitment=(
                ReceiptCommitment(
                    digest=self.digest,
                    expires_at=self.expiry,
                )
                if receipt
                else None
            ),
        )
        result = self.repository.accept(command)
        self.assertIsInstance(result, AttemptAccepted)
        return result

    def command(
        self,
        *,
        digest=None,
        channel_public_id=None,
        recipient_public_id=None,
        occurred_at=None,
        webhook_event_id="01J00000000000000000000000",
    ):
        return ConfirmReceiptCommand(
            capability_digest=digest or self.digest,
            channel_public_id=(
                channel_public_id or self.channel_public_id
            ),
            recipient_public_id=(
                recipient_public_id or self.recipient_public_id
            ),
            occurred_at=occurred_at or NOW,
            webhook_event_id=webhook_event_id,
        )

    # テストケース: processing配信へ有効な初回受取確認を記録する。
    # 期待値: 初回日時とevent IDだけを保存し、配信状態とLINE IDを変更しない。
    def test_confirm_receipt_records_first_event_without_changing_delivery(self):
        accepted = self.accept()
        DeliveryAttempt.objects.filter(pk=accepted.attempt_id).update(
            line_request_id="line-request",
            line_accepted_request_id="accepted-request",
        )

        result = self.repository.confirm_receipt(self.command())

        self.assertIsInstance(result, ReceiptRecorded)
        self.assertEqual(result.snapshot.receipt_status, "confirmed")
        attempt = DeliveryAttempt.objects.get(pk=accepted.attempt_id)
        self.assertEqual(attempt.receipt_confirmed_at, NOW)
        self.assertEqual(
            attempt.receipt_webhook_event_id,
            "01J00000000000000000000000",
        )
        self.assertEqual(attempt.status, "processing")
        self.assertEqual(attempt.line_request_id, "line-request")
        self.assertEqual(
            attempt.line_accepted_request_id,
            "accepted-request",
        )

    # テストケース: succeededとunknownの配信へ有効な受取確認を記録する。
    # 期待値: processing以外の許可状態でも配信結果を維持してconfirmedになる。
    def test_confirm_receipt_accepts_succeeded_and_unknown_delivery(self):
        for index, result in enumerate(
            (
                LinePushAccepted(
                    "line-request",
                    "accepted-request",
                ),
                LinePushUnknown("timeout_unknown"),
            ),
            start=6,
        ):
            with self.subTest(result=result.status):
                self.digest = format(index, "x") * 64
                accepted = self.accept()
                self.repository.finalize(
                    accepted.attempt_id,
                    result,
                    NOW + timedelta(seconds=1),
                )

                receipt = self.repository.confirm_receipt(
                    self.command(digest=self.digest)
                )

                self.assertIsInstance(receipt, ReceiptRecorded)
                attempt = DeliveryAttempt.objects.get(
                    pk=accepted.attempt_id
                )
                self.assertEqual(attempt.status, receipt.snapshot.status)
                if result.status == "accepted":
                    self.assertEqual(
                        attempt.line_request_id,
                        "line-request",
                    )
                    self.assertEqual(
                        attempt.line_accepted_request_id,
                        "accepted-request",
                    )
    # テストケース: 同じ配信へ同一または別event IDで再確認する。
    # 期待値: unchangedへ収束し、初回日時と初回event IDを上書きしない。
    def test_confirm_receipt_repeated_events_keep_first_confirmation(self):
        self.accept()
        first_at = NOW + timedelta(minutes=1)
        first = self.repository.confirm_receipt(
            self.command(occurred_at=first_at)
        )

        same = self.repository.confirm_receipt(
            self.command(
                occurred_at=first_at + timedelta(minutes=1),
            )
        )
        different = self.repository.confirm_receipt(
            self.command(
                occurred_at=self.expiry,
                webhook_event_id="01J11111111111111111111111",
            )
        )

        self.assertIsInstance(first, ReceiptRecorded)
        self.assertIsInstance(same, ReceiptUnchanged)
        self.assertIsInstance(different, ReceiptUnchanged)
        attempt = DeliveryAttempt.objects.get()
        self.assertEqual(attempt.receipt_confirmed_at, first_at)
        self.assertEqual(
            attempt.receipt_webhook_event_id,
            "01J00000000000000000000000",
        )

    # テストケース: digest、対象、期限、要求有無、failed状態の不正入力を処理する。
    # 期待値: 安全な拒否分類を返し、どのattemptも変更しない。
    def test_confirm_receipt_rejects_ineligible_commands_without_mutation(self):
        cases = (
            (
                "unmatched",
                lambda: self.command(digest="a" * 64),
                None,
            ),
            (
                "target_mismatch",
                lambda: self.command(channel_public_id=uuid4()),
                None,
            ),
            (
                "target_mismatch",
                lambda: self.command(recipient_public_id=uuid4()),
                None,
            ),
            (
                "expired",
                lambda: self.command(occurred_at=self.expiry),
                None,
            ),
            (
                "delivery_failed",
                self.command,
                LinePushRejected("permission"),
            ),
        )
        for index, (reason, command_factory, terminal) in enumerate(cases):
            with self.subTest(reason=reason, index=index):
                self.digest = format(index + 6, "x") * 64
                accepted = self.accept()
                if terminal is not None:
                    self.repository.finalize(
                        accepted.attempt_id,
                        terminal,
                        NOW + timedelta(seconds=1),
                    )
                before = DeliveryAttempt.objects.values().get(
                    pk=accepted.attempt_id
                )

                result = self.repository.confirm_receipt(
                    command_factory()
                )

                self.assertIsInstance(result, ReceiptRejected)
                self.assertEqual(result.reason, reason)
                after = DeliveryAttempt.objects.values().get(
                    pk=accepted.attempt_id
                )
                self.assertEqual(after, before)

    # テストケース: DB列へ保存できない長さのevent IDで確認する。
    # 期待値: DB例外を公開せずinvalidとして拒否し、attemptを変更しない。
    def test_confirm_receipt_rejects_oversized_event_id_safely(self):
        self.accept()

        result = self.repository.confirm_receipt(
            self.command(webhook_event_id="x" * 27)
        )

        self.assertEqual(result, ReceiptRejected("invalid"))
        attempt = DeliveryAttempt.objects.get()
        self.assertIsNone(attempt.receipt_confirmed_at)

    # テストケース: receipt未要求の配信に任意のcapability digestを提示する。
    # 期待値: 対応するcommitmentがないためunmatchedとなり、配信を変更しない。
    def test_confirm_receipt_rejects_delivery_without_commitment(self):
        accepted = self.accept(receipt=False)

        result = self.repository.confirm_receipt(self.command())

        self.assertEqual(result, ReceiptRejected("unmatched"))
        attempt = DeliveryAttempt.objects.get(pk=accepted.attempt_id)
        self.assertFalse(attempt.receipt_requested)
        self.assertIsNone(attempt.receipt_confirmed_at)


class DjangoAttemptRepositoryContractIntegrationTests(TestCase):
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
            subject="統合件名",
            body="統合本文",
            formatted_text="【統合件名】\n\n統合本文",
            fingerprint="4" * 64,
        )

    def command(
        self,
        *,
        operation_id=None,
        target=None,
        receipt_digest=None,
        identity=None,
    ):
        target = target or self.target
        identity = identity or self.owner_identity
        commitment = (
            ReceiptCommitment(
                digest=receipt_digest,
                expires_at=NOW + timedelta(hours=24),
            )
            if receipt_digest is not None
            else None
        )
        return AcceptedDeliveryCommand(
            operation_id=operation_id or uuid4(),
            owner=self.owner,
            owner_identity=identity,
            target=target,
            message=self.message,
            request_fingerprint=build_request_fingerprint(
                owner=self.owner,
                owner_identity=identity,
                channel_public_id=target.channel_public_id,
                recipient_public_id=target.recipient_public_id,
                message_fingerprint=self.message.fingerprint,
                receipt_requested=commitment is not None,
            ),
            receipt_commitment=commitment,
        )

    # テストケース: operation再利用とtarget／receipt option差を一連のaccept契約で扱う。
    # 期待値: 同一requestはcanonical行へ収束し、同一operationの差分は競合、別requestは別行になる。
    def test_accept_contract_distinguishes_operation_target_and_option(self):
        operation_id = uuid4()
        original = self.command(operation_id=operation_id)
        changed_target = LinkedTargetSnapshot(
            channel_public_id=self.target.channel_public_id,
            channel_label=self.target.channel_label,
            recipient_public_id=uuid4(),
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )

        accepted = self.repository.accept(original)
        same_operation = self.repository.accept(original)
        same_request_new_operation = self.repository.accept(
            self.command(operation_id=uuid4())
        )
        target_conflict = self.repository.accept(
            self.command(
                operation_id=operation_id,
                target=changed_target,
            )
        )
        option_conflict = self.repository.accept(
            self.command(
                operation_id=operation_id,
                receipt_digest="6" * 64,
            )
        )
        target_variant = self.repository.accept(
            self.command(target=changed_target)
        )
        option_variant = self.repository.accept(
            self.command(receipt_digest="7" * 64)
        )

        self.assertIsInstance(accepted, AttemptAccepted)
        self.assertIsInstance(same_operation, ExistingAttempt)
        self.assertIsInstance(same_request_new_operation, ExistingAttempt)
        self.assertEqual(
            same_request_new_operation.snapshot.operation_id,
            operation_id,
        )
        self.assertIsInstance(target_conflict, AttemptConflict)
        self.assertIsInstance(option_conflict, AttemptConflict)
        self.assertIsInstance(target_variant, AttemptAccepted)
        self.assertIsInstance(option_variant, AttemptAccepted)
        self.assertEqual(DeliveryAttempt.objects.count(), 3)

    # テストケース: linked attempt受理後にowner連携が消え、配信確定と受取確認が続く。
    # 期待値: 保存snapshotだけでstatusを返し、first terminalとreceiptを直交して不変に保つ。
    def test_linked_status_finalize_and_receipt_survive_unlink(self):
        identity = LineIdentity.objects.create(
            public_id=self.owner_identity.public_id,
            provider_id="provider",
            subject="U" + "1" * 32,
            display_name="送信者",
        )
        OwnerAccount.objects.update_or_create(
            slot=self.owner.slot,
            defaults={
                "state": OwnerAccount.State.ACTIVE,
                "identity": identity,
            },
        )
        accepted = self.repository.accept(
            self.command(receipt_digest="6" * 64)
        )

        OwnerAccount.objects.filter(slot=self.owner.slot).update(
            state=OwnerAccount.State.VACANT,
            identity=None,
        )
        identity.delete()
        before_finalize = self.repository.get_for_owner(
            self.owner.slot,
            accepted.snapshot.operation_id,
        )
        first_completed_at = NOW + timedelta(seconds=1)
        first_terminal = self.repository.finalize(
            accepted.attempt_id,
            LinePushAccepted("line-request", "accepted-request"),
            first_completed_at,
        )
        later_terminal = self.repository.finalize(
            accepted.attempt_id,
            LinePushUnknown("timeout_unknown"),
            NOW + timedelta(seconds=2),
        )
        receipt = self.repository.confirm_receipt(
            ConfirmReceiptCommand(
                capability_digest="6" * 64,
                channel_public_id=self.target.channel_public_id,
                recipient_public_id=self.target.recipient_public_id,
                occurred_at=NOW + timedelta(seconds=3),
                webhook_event_id="01J00000000000000000000000",
            )
        )
        final_status = self.repository.get_for_owner(
            self.owner.slot,
            accepted.snapshot.operation_id,
        )

        self.assertFalse(LineIdentity.objects.exists())
        self.assertEqual(before_finalize.target, self.target)
        self.assertEqual(first_terminal.status, "succeeded")
        self.assertEqual(later_terminal.status, "succeeded")
        self.assertEqual(later_terminal.completed_at, first_completed_at)
        self.assertEqual(later_terminal.line_request_id, "line-request")
        self.assertIsInstance(receipt, ReceiptRecorded)
        self.assertEqual(final_status.status, "succeeded")
        self.assertEqual(final_status.receipt_status, "confirmed")
        self.assertEqual(final_status.completed_at, first_completed_at)
        self.assertEqual(final_status.line_request_id, "line-request")
        self.assertEqual(
            final_status.line_accepted_request_id,
            "accepted-request",
        )

    # テストケース: failedとunknownを確定した別attemptへreceiptを提示する。
    # 期待値: failedは無変更で拒否し、unknownは確認を記録して終端結果を維持する。
    def test_receipt_rejects_failed_but_records_unknown_independently(self):
        failed = self.repository.accept(
            self.command(
                receipt_digest="6" * 64,
                identity=OwnerIdentitySnapshot(uuid4()),
            )
        )
        unknown = self.repository.accept(
            self.command(
                receipt_digest="7" * 64,
                identity=OwnerIdentitySnapshot(uuid4()),
            )
        )
        failed_at = NOW + timedelta(seconds=1)
        unknown_at = NOW + timedelta(seconds=2)
        self.repository.finalize(
            failed.attempt_id,
            LinePushRejected("permission"),
            failed_at,
        )
        self.repository.finalize(
            unknown.attempt_id,
            LinePushUnknown("response_unknown"),
            unknown_at,
        )

        failed_receipt = self.repository.confirm_receipt(
            ConfirmReceiptCommand(
                capability_digest="6" * 64,
                channel_public_id=self.target.channel_public_id,
                recipient_public_id=self.target.recipient_public_id,
                occurred_at=NOW + timedelta(seconds=3),
                webhook_event_id="01J00000000000000000000000",
            )
        )
        unknown_receipt = self.repository.confirm_receipt(
            ConfirmReceiptCommand(
                capability_digest="7" * 64,
                channel_public_id=self.target.channel_public_id,
                recipient_public_id=self.target.recipient_public_id,
                occurred_at=NOW + timedelta(seconds=4),
                webhook_event_id="01J11111111111111111111111",
            )
        )

        failed_attempt = DeliveryAttempt.objects.get(pk=failed.attempt_id)
        unknown_attempt = DeliveryAttempt.objects.get(pk=unknown.attempt_id)
        self.assertEqual(failed_receipt, ReceiptRejected("delivery_failed"))
        self.assertIsNone(failed_attempt.receipt_confirmed_at)
        self.assertEqual(failed_attempt.status, "failed")
        self.assertEqual(failed_attempt.completed_at, failed_at)
        self.assertIsInstance(unknown_receipt, ReceiptRecorded)
        self.assertEqual(unknown_attempt.status, "unknown")
        self.assertEqual(unknown_attempt.failure_type, "response_unknown")
        self.assertEqual(unknown_attempt.completed_at, unknown_at)
        self.assertEqual(
            unknown_attempt.receipt_webhook_event_id,
            "01J11111111111111111111111",
        )

    # テストケース: 0001由来のfixed終端行を0002でowner scopeへbackfillした形で照会する。
    # 期待値: identity解決不能でも、backfill値と旧message・結果・ID・時刻を同値で返す。
    def test_legacy_fixed_rows_share_owner_scoped_repository_contract(self):
        succeeded_operation = uuid4()
        failed_operation = uuid4()
        succeeded_at = NOW - timedelta(days=2)
        failed_at = NOW - timedelta(days=1)
        succeeded_accepted_at = succeeded_at - timedelta(seconds=1)
        failed_accepted_at = failed_at - timedelta(seconds=1)
        succeeded = DeliveryAttempt.objects.create(
            operation_id=succeeded_operation,
            subject="旧成功件名",
            body="旧成功本文",
            formatted_text="【旧成功件名】\n\n旧成功本文",
            content_fingerprint="a" * 64,
            active_content_fingerprint=None,
            request_fingerprint="a" * 64,
            active_request_fingerprint=None,
            target_mode=DeliveryAttempt.TargetMode.FIXED_USER,
            owner_principal_slot=1,
            owner_identity_public_id=None,
            status=DeliveryAttempt.Status.SUCCEEDED,
            line_request_id="legacy-line-request",
            line_accepted_request_id="legacy-accepted-request",
            accepted_at=succeeded_accepted_at,
            processing_expires_at=succeeded_at,
            sent_at=succeeded_at,
            completed_at=succeeded_at,
        )
        failed = DeliveryAttempt.objects.create(
            operation_id=failed_operation,
            subject="旧失敗件名",
            body="旧失敗本文",
            formatted_text="【旧失敗件名】\n\n旧失敗本文",
            content_fingerprint="b" * 64,
            active_content_fingerprint=None,
            request_fingerprint="b" * 64,
            active_request_fingerprint=None,
            target_mode=DeliveryAttempt.TargetMode.FIXED_USER,
            owner_principal_slot=1,
            owner_identity_public_id=None,
            status=DeliveryAttempt.Status.FAILED,
            failure_type=DeliveryAttempt.FailureType.SERVICE_UNAVAILABLE,
            accepted_at=failed_accepted_at,
            processing_expires_at=failed_at,
            failed_at=failed_at,
            completed_at=failed_at,
        )

        succeeded_snapshot = self.repository.get_for_owner(
            1,
            succeeded_operation,
        )
        failed_snapshot = self.repository.get_for_owner(
            1,
            failed_operation,
        )

        self.assertEqual(succeeded_snapshot.target.mode, "fixed_user")
        self.assertEqual(
            succeeded_snapshot.operation_id,
            succeeded_operation,
        )
        self.assertEqual(succeeded_snapshot.owner, OwnerPrincipal(1))
        self.assertIsNone(succeeded_snapshot.owner_identity)
        self.assertEqual(succeeded_snapshot.message.subject, "旧成功件名")
        self.assertEqual(succeeded_snapshot.message.body, "旧成功本文")
        self.assertEqual(
            succeeded_snapshot.message.formatted_text,
            "【旧成功件名】\n\n旧成功本文",
        )
        self.assertEqual(
            succeeded_snapshot.message.fingerprint,
            "a" * 64,
        )
        self.assertEqual(succeeded_snapshot.status, "succeeded")
        self.assertIsNone(succeeded_snapshot.failure)
        self.assertEqual(
            succeeded_snapshot.accepted_at,
            succeeded_accepted_at,
        )
        self.assertEqual(
            succeeded_snapshot.line_request_id,
            "legacy-line-request",
        )
        self.assertEqual(
            succeeded_snapshot.line_accepted_request_id,
            "legacy-accepted-request",
        )
        self.assertEqual(succeeded_snapshot.completed_at, succeeded_at)
        self.assertEqual(failed_snapshot.target.mode, "fixed_user")
        self.assertEqual(failed_snapshot.operation_id, failed_operation)
        self.assertEqual(failed_snapshot.owner, OwnerPrincipal(1))
        self.assertIsNone(failed_snapshot.owner_identity)
        self.assertEqual(failed_snapshot.message.subject, "旧失敗件名")
        self.assertEqual(failed_snapshot.message.body, "旧失敗本文")
        self.assertEqual(
            failed_snapshot.message.formatted_text,
            "【旧失敗件名】\n\n旧失敗本文",
        )
        self.assertEqual(failed_snapshot.message.fingerprint, "b" * 64)
        self.assertEqual(failed_snapshot.status, "failed")
        self.assertEqual(
            failed_snapshot.failure,
            "service_unavailable",
        )
        self.assertEqual(
            failed_snapshot.accepted_at,
            failed_accepted_at,
        )
        self.assertEqual(failed_snapshot.completed_at, failed_at)
        self.assertIsNone(failed_snapshot.line_request_id)
        self.assertIsNone(failed_snapshot.line_accepted_request_id)
        succeeded.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual(succeeded.owner_principal_slot, 1)
        self.assertEqual(failed.owner_principal_slot, 1)
        self.assertEqual(
            succeeded.request_fingerprint,
            succeeded.content_fingerprint,
        )
        self.assertEqual(
            failed.request_fingerprint,
            failed.content_fingerprint,
        )
        self.assertEqual(succeeded.sent_at, succeeded_at)
        self.assertEqual(failed.failed_at, failed_at)
