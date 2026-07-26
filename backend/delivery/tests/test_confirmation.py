from datetime import datetime, timedelta, timezone
from uuid import UUID

from django.test import SimpleTestCase

from delivery.confirmation import (
    ConfirmationRejected,
    ConfirmationService,
    ConfirmationVerified,
)
from delivery.types import (
    ConfirmationSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
    TargetRevision,
)


NOW = datetime(2026, 7, 26, 1, 2, 3, 456789, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class ConfirmationServiceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.clock = _Clock(NOW)
        self.service = ConfirmationService(clock=self.clock)

    # テストケース: 受取確認の有無ごとにpreview時点の期限を生成する
    # 期待値: 要求時は24時間後、未要求時はnullになり同じ注入clockだけを使う
    def test_receipt_expiry_is_fixed_at_preview_time(self):
        self.assertEqual(
            self.service.receipt_expires_at(True),
            NOW + timedelta(hours=24),
        )
        self.assertIsNone(self.service.receipt_expires_at(False))

    # テストケース: owner・identity・target・message・receiptを結ぶtokenを発行する
    # 期待値: 同じsnapshotだけが10分以内にtyped verified resultへなる
    def test_issue_and_verify_same_snapshot(self):
        snapshot = self._snapshot(receipt_requested=True)

        issued = self.service.issue(snapshot)
        verified = self.service.verify(issued.token, snapshot)

        self.assertIsInstance(verified, ConfirmationVerified)
        self.assertEqual(verified.snapshot, snapshot)
        self.assertEqual(issued.receipt_expires_at, NOW + timedelta(hours=24))

    # テストケース: 署名payloadをtest専用decoderで観測する
    # 期待値: versionと全照合digestだけを持ち、本文・表示名・LINE subjectを含まない
    def test_payload_is_versioned_and_pii_free(self):
        snapshot = self._snapshot(receipt_requested=True)

        issued = self.service.issue(snapshot)
        payload = self.service.decode_for_test(issued.token)

        self.assertEqual(
            payload,
            {
                "v": 1,
                "owner": 7,
                "identity": "11111111-1111-4111-8111-111111111111",
                "channel": "22222222-2222-4222-8222-222222222222",
                "recipient": "33333333-3333-4333-8333-333333333333",
                "target_revision": "a" * 64,
                "message_fingerprint": "b" * 64,
                "receipt_requested": True,
                "receipt_expires_at": "2026-07-27T01:02:03.456789Z",
            },
        )
        serialized = repr(payload)
        for canary in (
            "本文canary",
            "表示名canary",
            "U0123456789abcdef",
            "receipt-capability-canary",
        ):
            self.assertNotIn(canary, serialized)

    # テストケース: 受取確認なしのsnapshotを発行する
    # 期待値: payloadと公開resultの期限はnullで同じsnapshotを検証できる
    def test_receipt_not_requested_has_no_expiry(self):
        snapshot = self._snapshot(receipt_requested=False)

        issued = self.service.issue(snapshot)

        self.assertIsNone(issued.receipt_expires_at)
        self.assertIsNone(
            self.service.decode_for_test(issued.token)["receipt_expires_at"]
        )
        self.assertIsInstance(
            self.service.verify(issued.token, snapshot),
            ConfirmationVerified,
        )

    # テストケース: 発行後に注入clockを10分境界の外へ進める
    # 期待値: 10分ちょうどは有効、1秒超過はtyped expired rejectionになる
    def test_confirmation_max_age_uses_injected_clock(self):
        snapshot = self._snapshot(receipt_requested=True)
        issued = self.service.issue(snapshot)

        self.clock.current = NOW + timedelta(minutes=10)
        self.assertIsInstance(
            self.service.verify(issued.token, snapshot),
            ConfirmationVerified,
        )
        self.clock.current = NOW + timedelta(minutes=10, seconds=1)
        rejected = self.service.verify(issued.token, snapshot)

        self.assertIsInstance(rejected, ConfirmationRejected)
        self.assertEqual(rejected.reason, "expired")

    def _snapshot(self, *, receipt_requested: bool) -> ConfirmationSnapshot:
        return ConfirmationSnapshot(
            owner=OwnerPrincipal(7),
            owner_identity=OwnerIdentitySnapshot(
                UUID("11111111-1111-4111-8111-111111111111")
            ),
            channel_public_id=UUID(
                "22222222-2222-4222-8222-222222222222"
            ),
            recipient_public_id=UUID(
                "33333333-3333-4333-8333-333333333333"
            ),
            target_revision=TargetRevision("a" * 64),
            message_fingerprint="b" * 64,
            receipt_requested=receipt_requested,
            receipt_expires_at=self.service.receipt_expires_at(
                receipt_requested
            ),
        )
