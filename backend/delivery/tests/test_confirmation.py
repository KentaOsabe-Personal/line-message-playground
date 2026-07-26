from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

from django.test import SimpleTestCase

from delivery.confirmation import (
    ConfirmationRejected,
    ConfirmationService,
    ConfirmationVerified,
)
from delivery.formatters import format_message
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

    # テストケース: preview後に確認snapshotの各入力軸を一つずつ変更する
    # 期待値: channel・recipient・件名・本文・owner・identity・revision・期限の差を全て拒否する
    def test_rejects_each_changed_confirmation_axis(self):
        subject = "確認済み件名"
        body = "確認済み本文"
        snapshot = replace(
            self._snapshot(receipt_requested=True),
            message_fingerprint=format_message(subject, body).fingerprint,
        )
        issued = self.service.issue(snapshot)
        changed_snapshots = {
            "別owner": replace(snapshot, owner=OwnerPrincipal(8)),
            "再連携後identity": replace(
                snapshot,
                owner_identity=OwnerIdentitySnapshot(
                    UUID("44444444-4444-4444-8444-444444444444")
                ),
            ),
            "channel": replace(
                snapshot,
                channel_public_id=UUID(
                    "55555555-5555-4555-8555-555555555555"
                ),
            ),
            "recipient": replace(
                snapshot,
                recipient_public_id=UUID(
                    "66666666-6666-4666-8666-666666666666"
                ),
            ),
            "target revision": replace(
                snapshot,
                target_revision=TargetRevision("c" * 64),
            ),
            "件名": replace(
                snapshot,
                message_fingerprint=format_message(
                    "変更後件名", body
                ).fingerprint,
            ),
            "本文": replace(
                snapshot,
                message_fingerprint=format_message(
                    subject, "変更後本文"
                ).fingerprint,
            ),
            "receipt expiry": replace(
                snapshot,
                receipt_expires_at=NOW
                + timedelta(hours=24, seconds=1),
            ),
        }

        for axis, changed in changed_snapshots.items():
            with self.subTest(axis=axis):
                rejected = self.service.verify(issued.token, changed)

                self.assertEqual(
                    rejected,
                    ConfirmationRejected("mismatch"),
                )

    # テストケース: preview後に受取確認オプションだけを切り替える
    # 期待値: optionとそれに従属する期限の組を完全一致で比較し、再previewを要求する
    def test_rejects_changed_receipt_option(self):
        without_receipt = self._snapshot(receipt_requested=False)
        issued = self.service.issue(without_receipt)
        with_receipt = replace(
            without_receipt,
            receipt_requested=True,
            receipt_expires_at=self.service.receipt_expires_at(True),
        )

        rejected = self.service.verify(issued.token, with_receipt)

        self.assertEqual(rejected, ConfirmationRejected("mismatch"))

    # テストケース: unlink/relinkまたは状態往復後に古い確認を再利用する
    # 期待値: identity UUIDまたは更新済みrevisionの差で古い確認を拒否する
    def test_rejects_old_confirmation_after_relink_or_state_round_trip(self):
        snapshot = self._snapshot(receipt_requested=False)
        issued = self.service.issue(snapshot)
        current_snapshots = {
            "unlink/relink": replace(
                snapshot,
                owner_identity=OwnerIdentitySnapshot(
                    UUID("77777777-7777-4777-8777-777777777777")
                ),
            ),
            "状態往復": replace(
                snapshot,
                target_revision=TargetRevision("d" * 64),
            ),
        }

        for transition, current in current_snapshots.items():
            with self.subTest(transition=transition):
                self.assertEqual(
                    self.service.verify(issued.token, current),
                    ConfirmationRejected("mismatch"),
                )

    # テストケース: token改変と10分超過を同じ公開verify契約へ入力する
    # 期待値: signing例外・署名時刻・tokenを露出せずsafeなtyped理由だけを返す
    def test_tamper_and_expiry_return_only_safe_rejection_codes(self):
        snapshot = self._snapshot(receipt_requested=True)
        issued = self.service.issue(snapshot)
        separator = issued.token.rfind(":")
        signature = issued.token[separator + 1 :]
        tampered_character = "A" if signature[0] != "A" else "B"
        tampered = (
            issued.token[: separator + 1]
            + tampered_character
            + signature[1:]
        )

        invalid = self.service.verify(tampered, snapshot)
        self.clock.current = NOW + timedelta(minutes=10, microseconds=1)
        expired = self.service.verify(issued.token, snapshot)

        self.assertEqual(invalid, ConfirmationRejected("invalid"))
        self.assertEqual(expired, ConfirmationRejected("expired"))
        for rejected in (invalid, expired):
            serialized = repr(rejected)
            self.assertNotIn(issued.token, serialized)
            self.assertNotIn("Signature", serialized)
            self.assertNotIn(NOW.isoformat(), serialized)

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
