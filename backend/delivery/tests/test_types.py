import json
import pickle
from pathlib import Path
from dataclasses import FrozenInstanceError, asdict, fields
from datetime import datetime, timedelta, timezone
from typing import get_args
from uuid import uuid4

from django.test import SimpleTestCase

from lineaccounts.types import LineSubject
from linechannels.types import AccessToken

from delivery.types import (
    AttemptAccepted,
    AttemptConflict,
    ConfirmationSnapshot,
    DeliverySnapshot,
    FixedTargetSnapshot,
    LinePushAccepted,
    LinePushRejected,
    LinePushResult,
    LinePushUnknown,
    LinkedTargetSnapshot,
    LiveDeliveryTarget,
    MessageSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
    PushLinkedRecipientCommand,
    ReceiptCapability,
    ReceiptCapabilityCandidate,
    ReceiptCommitment,
    RejectedPushFailureType,
    ReceiptRecorded,
    ReceiptRejected,
    ReceiptStatus,
    ReceiptUnchanged,
    RequestFingerprint,
    TargetRevision,
    UnknownPushFailureType,
)


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


class DeliveryDomainTypeTests(SimpleTestCase):
    # テストケース: linked対象のowner・identity・送信時snapshotを生成して変更を試みる
    # 期待値: Model非依存の型付き不変値として保持され、変更は拒否される
    def test_linked_target_contract_is_typed_and_immutable(self):
        principal = OwnerPrincipal(slot=1)
        identity = OwnerIdentitySnapshot(public_id=uuid4())
        target = LinkedTargetSnapshot(
            channel_public_id=uuid4(),
            channel_label="学習用チャネル",
            recipient_public_id=uuid4(),
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        snapshot = DeliverySnapshot(
            operation_id=uuid4(),
            owner=principal,
            owner_identity=identity,
            target=target,
            message=MessageSnapshot(
                subject="件名",
                body="本文",
                formatted_text="件名\n\n本文",
                fingerprint="a" * 64,
            ),
            status="processing",
            accepted_at=NOW,
            completed_at=None,
            line_request_id=None,
            line_accepted_request_id=None,
            failure=None,
            receipt_status="pending",
            receipt_expires_at=NOW + timedelta(hours=24),
            receipt_confirmed_at=None,
            receipt_webhook_event_id=None,
        )

        self.assertEqual(snapshot.target.mode, "linked_recipient")
        self.assertEqual(snapshot.owner.slot, 1)
        self.assertEqual(snapshot.owner_identity, identity)
        with self.assertRaises(FrozenInstanceError):
            snapshot.status = "succeeded"

    # テストケース: legacy fixed対象とlinked対象を同じtarget契約へ格納する
    # 期待値: modeで安全に識別でき、fixed側はlinkedのidentityやrecipient UUIDを要求しない
    def test_fixed_and_linked_target_snapshots_are_distinct(self):
        fixed = FixedTargetSnapshot()
        linked = LinkedTargetSnapshot(
            channel_public_id=uuid4(),
            channel_label="運用名",
            recipient_public_id=uuid4(),
            channel_active=False,
            recipient_enabled=True,
            friendship_state="unknown",
        )

        self.assertEqual(fixed.mode, "fixed_user")
        self.assertEqual(linked.mode, "linked_recipient")
        self.assertEqual(
            {field.name for field in fields(fixed)},
            {"mode"},
        )

    # テストケース: preview用snapshotへowner・target・message・receipt軸を結び付ける
    # 期待値: PIIを含まない識別値だけで確認済み操作を表現できる
    def test_confirmation_snapshot_is_pii_free(self):
        snapshot = ConfirmationSnapshot(
            owner=OwnerPrincipal(1),
            owner_identity=OwnerIdentitySnapshot(uuid4()),
            channel_public_id=uuid4(),
            recipient_public_id=uuid4(),
            target_revision=TargetRevision("b" * 64),
            message_fingerprint="c" * 64,
            receipt_requested=True,
            receipt_expires_at=NOW + timedelta(hours=24),
        )

        self.assertEqual(
            {field.name for field in fields(snapshot)},
            {
                "owner",
                "owner_identity",
                "channel_public_id",
                "recipient_public_id",
                "target_revision",
                "message_fingerprint",
                "receipt_requested",
                "receipt_expires_at",
            },
        )
        self.assertNotIn("display", repr(snapshot).lower())
        self.assertNotIn("subject", repr(snapshot).lower())

    # テストケース: access token・LINE user ID・receipt capability canaryをgateway commandへ渡す
    # 期待値: commandを含む表示・汎用serialization・pickleへ生値が出ない
    def test_secret_and_pii_boundaries_are_redacted_and_non_serializable(self):
        token_canary = "access-token-canary"
        subject_canary = "U" + "a" * 32
        receipt_canary = "receipt-capability-canary"
        capability = ReceiptCapability(receipt_canary)
        command = PushLinkedRecipientCommand(
            operation_id=uuid4(),
            access_token=AccessToken(token_canary),
            subject=LineSubject(subject_canary),
            text="formatted text",
            receipt_capability=capability,
        )

        for value in (capability, command):
            rendered = repr(value) + str(value)
            self.assertNotIn(token_canary, rendered)
            self.assertNotIn(subject_canary, rendered)
            self.assertNotIn(receipt_canary, rendered)
            with self.assertRaises(TypeError):
                json.dumps(value)
            with self.assertRaisesRegex(TypeError, "serialization is disabled"):
                pickle.dumps(value)
        self.assertEqual(
            capability.reveal_for_push_action(), receipt_canary
        )

    # テストケース: capability候補をdigestだけの永続化commitmentへ分離する
    # 期待値: repository向けcommitmentはraw値を持たず、candidateもraw値を表示しない
    def test_receipt_candidate_separates_raw_value_from_commitment(self):
        raw_canary = "receipt-secret-canary"
        commitment = ReceiptCommitment(
            digest="d" * 64,
            expires_at=NOW + timedelta(hours=24),
        )
        candidate = ReceiptCapabilityCandidate(
            capability=ReceiptCapability(raw_canary),
            commitment=commitment,
        )

        self.assertEqual(
            {field.name for field in fields(commitment)},
            {"digest", "expires_at"},
        )
        self.assertNotIn(raw_canary, repr(candidate))
        with self.assertRaisesRegex(TypeError, "serialization is disabled"):
            pickle.dumps(candidate)

    # テストケース: live targetへredacted subjectを保持してreprとpickleを確認する
    # 期待値: service間で対象を型付き利用できる一方、LINE user IDは観測面へ出ない
    def test_live_target_hides_line_subject(self):
        canary = "U" + "b" * 32
        target = LiveDeliveryTarget(
            owner_identity=OwnerIdentitySnapshot(uuid4()),
            provider_id="1234567890",
            snapshot=LinkedTargetSnapshot(
                channel_public_id=uuid4(),
                channel_label="運用名",
                recipient_public_id=uuid4(),
                channel_active=True,
                recipient_enabled=True,
                friendship_state="friend",
            ),
            revision=TargetRevision("e" * 64),
            subject=LineSubject(canary),
            delivery_available=True,
        )

        self.assertNotIn(canary, repr(target))
        with self.assertRaisesRegex(TypeError, "serialization is disabled"):
            pickle.dumps(target)

    # テストケース: gatewayのaccepted・rejected・unknown結果を列挙する
    # 期待値: 外部SDK応答や例外を持たない閉じた安全な結果unionになる
    def test_line_push_results_are_closed_and_safe(self):
        results = (
            LinePushAccepted(request_id="request-id", accepted_request_id=None),
            LinePushRejected(failure_type="rate_limited"),
            LinePushUnknown(failure_type="timeout_unknown"),
        )

        self.assertEqual(
            set(get_args(LinePushResult)),
            {LinePushAccepted, LinePushRejected, LinePushUnknown},
        )
        self.assertEqual(
            {result.status for result in results},
            {"accepted", "rejected", "unknown"},
        )
        self.assertNotIn("response", {field.name for result in results for field in fields(result)})
        self.assertNotIn("exception", {field.name for result in results for field in fields(result)})

    # テストケース: rejectedとunknownへ状態に合わないfailure分類を渡し曖昧外部結果も生成する
    # 期待値: rejectedは明示拒否だけ、unknownはservice/timeout/response不明だけを許可する
    def test_line_push_failure_types_are_closed_by_result_state(self):
        with self.assertRaises(ValueError):
            LinePushRejected(failure_type="timeout_unknown")
        with self.assertRaises(ValueError):
            LinePushUnknown(failure_type="authentication")

        self.assertEqual(
            {
                LinePushUnknown(failure_type="service_unknown").failure_type,
                LinePushUnknown(failure_type="response_unknown").failure_type,
            },
            {"service_unknown", "response_unknown"},
        )
        self.assertEqual(
            set(get_args(RejectedPushFailureType)),
            {
                "invalid_request",
                "authentication",
                "permission",
                "conflict",
                "rate_limited",
            },
        )
        self.assertEqual(
            set(get_args(UnknownPushFailureType)),
            {"service_unknown", "timeout_unknown", "response_unknown"},
        )

    # テストケース: message単体とdelivery snapshotを汎用dataclass変換する
    # 期待値: asdictで件名・本文・整形済みtextの生値を取得できない
    def test_message_snapshot_rejects_generic_dataclass_conversion(self):
        message = MessageSnapshot(
            subject="subject-canary",
            body="body-canary",
            formatted_text="formatted-canary",
            fingerprint="9" * 64,
        )
        with self.assertRaises(TypeError):
            asdict(message)

        snapshot = DeliverySnapshot(
            operation_id=uuid4(),
            owner=OwnerPrincipal(1),
            owner_identity=None,
            target=FixedTargetSnapshot(),
            message=message,
            status="processing",
            accepted_at=NOW,
            completed_at=None,
            line_request_id=None,
            line_accepted_request_id=None,
            failure=None,
            receipt_status="not_requested",
            receipt_expires_at=None,
            receipt_confirmed_at=None,
            receipt_webhook_event_id=None,
        )

        with self.assertRaisesRegex(TypeError, "serialization is disabled"):
            asdict(snapshot)

    # テストケース: accepted request IDを持つunknown配信snapshotを生成する
    # 期待値: request IDと別軸でaccepted request IDを不変に保持できる
    def test_delivery_snapshot_retains_accepted_request_id(self):
        snapshot = DeliverySnapshot(
            operation_id=uuid4(),
            owner=OwnerPrincipal(1),
            owner_identity=None,
            target=FixedTargetSnapshot(),
            message=MessageSnapshot(
                subject="legacy",
                body="body",
                formatted_text="legacy\n\nbody",
                fingerprint="8" * 64,
            ),
            status="unknown",
            accepted_at=NOW,
            completed_at=NOW,
            line_request_id=None,
            line_accepted_request_id="accepted-request-id",
            failure="response_unknown",
            receipt_status="confirmed",
            receipt_expires_at=NOW + timedelta(hours=24),
            receipt_confirmed_at=NOW + timedelta(minutes=1),
            receipt_webhook_event_id="01J00000000000000000000000",
        )

        self.assertEqual(
            snapshot.line_accepted_request_id,
            "accepted-request-id",
        )
        self.assertEqual(snapshot.status, "unknown")
        self.assertEqual(snapshot.receipt_status, "confirmed")
        with self.assertRaises(FrozenInstanceError):
            snapshot.line_accepted_request_id = "replacement"

    # テストケース: receipt記録結果とattempt受付結果を構築する
    # 期待値: 更新・不変・拒否および新規・既存・競合を閉じた型で識別できる
    def test_attempt_and_receipt_results_are_typed(self):
        snapshot = self._fixed_snapshot()

        self.assertEqual(AttemptAccepted(1, snapshot).status, "accepted")
        self.assertEqual(AttemptConflict().status, "conflict")
        self.assertEqual(
            {
                ReceiptRecorded(snapshot).status,
                ReceiptUnchanged(snapshot).status,
                ReceiptRejected("unmatched").status,
            },
            {"recorded", "unchanged", "rejected"},
        )
        self.assertEqual(
            set(get_args(ReceiptStatus)),
            {"not_requested", "pending", "confirmed", "expired"},
        )

    # テストケース: hash・owner slot・receipt時刻の不正な組合せを生成する
    # 期待値: domain生成時に安全なValueErrorとして拒否する
    def test_domain_values_reject_invalid_invariants(self):
        with self.assertRaises(ValueError):
            OwnerPrincipal(0)
        with self.assertRaises(ValueError):
            TargetRevision("not-a-digest")
        with self.assertRaises(ValueError):
            RequestFingerprint("A" * 64)
        with self.assertRaises(ValueError):
            ConfirmationSnapshot(
                owner=OwnerPrincipal(1),
                owner_identity=OwnerIdentitySnapshot(uuid4()),
                channel_public_id=uuid4(),
                recipient_public_id=uuid4(),
                target_revision=TargetRevision("f" * 64),
                message_fingerprint="1" * 64,
                receipt_requested=False,
                receipt_expires_at=NOW,
            )

    # テストケース: delivery domain型moduleのimport境界を静的に確認する
    # 期待値: Django ModelやLINE外部SDK型へ依存せず、公開された境界値だけを利用する
    def test_domain_types_do_not_import_models_or_external_sdk(self):
        source = (
            Path(__file__).resolve().parents[1] / "types.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("delivery.models", source)
        self.assertNotIn("from .models", source)
        self.assertNotIn("linebot", source)
        self.assertNotIn("django.db", source)

    @staticmethod
    def _fixed_snapshot():
        return DeliverySnapshot(
            operation_id=uuid4(),
            owner=OwnerPrincipal(1),
            owner_identity=None,
            target=FixedTargetSnapshot(),
            message=MessageSnapshot(
                subject="legacy",
                body="body",
                formatted_text="legacy\n\nbody",
                fingerprint="2" * 64,
            ),
            status="succeeded",
            accepted_at=NOW,
            completed_at=NOW,
            line_request_id="request-id",
            line_accepted_request_id=None,
            failure=None,
            receipt_status="not_requested",
            receipt_expires_at=None,
            receipt_confirmed_at=None,
            receipt_webhook_event_id=None,
        )
