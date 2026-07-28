import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

from django.core import signing
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

    # テストケース: 発行後に注入clockを10分境界の両側へ進める
    # 期待値: 10分ちょうどは有効、1マイクロ秒超過でtyped expired rejectionになる
    def test_confirmation_max_age_uses_injected_clock(self):
        snapshot = self._snapshot(receipt_requested=True)
        issued = self.service.issue(snapshot)

        self.clock.current = NOW + timedelta(minutes=10)
        self.assertIsInstance(
            self.service.verify(issued.token, snapshot),
            ConfirmationVerified,
        )
        self.clock.current = NOW + timedelta(
            minutes=10,
            microseconds=1,
        )
        rejected = self.service.verify(issued.token, snapshot)

        self.assertIsInstance(rejected, ConfirmationRejected)
        self.assertEqual(rejected.reason, "expired")

    # テストケース: preview時点のreceipt期限を許容範囲の境界値で発行する
    # 期待値: 24時間ちょうどだけを許可し、現在時刻以下と1マイクロ秒超過を拒否する
    def test_issue_enforces_receipt_expiry_contract_at_microsecond_boundary(
        self,
    ):
        valid = replace(
            self._snapshot(receipt_requested=True),
            receipt_expires_at=NOW + timedelta(hours=24),
        )

        issued = self.service.issue(valid)

        self.assertEqual(
            issued.receipt_expires_at,
            NOW + timedelta(hours=24),
        )
        invalid_expiries = (
            NOW,
            NOW - timedelta(microseconds=1),
            NOW + timedelta(hours=24, microseconds=1),
        )
        for expiry in invalid_expiries:
            with self.subTest(expiry=expiry):
                with self.assertRaisesRegex(
                    ValueError,
                    "^invalid receipt expiry$",
                ):
                    self.service.issue(
                        replace(valid, receipt_expires_at=expiry)
                    )

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

    # テストケース: signing層が秘密canary入りの署名例外を返す
    # 期待値: cause chainやlogへ転送せず、公開resultをinvalidだけへ縮約する
    def test_signing_error_details_are_not_exposed_or_logged(self):
        snapshot = self._snapshot(receipt_requested=True)
        secret_error = signing.BadSignature(
            "本文canary 表示名canary U0123456789abcdef "
            "receipt-capability-canary"
        )

        with patch.object(
            self.service._signer,
            "unsign_object",
            side_effect=secret_error,
        ):
            with self.assertNoLogs("delivery.confirmation", level="DEBUG"):
                rejected = self.service.verify(
                    "confirmation-token-canary",
                    snapshot,
                )

        self.assertEqual(rejected, ConfirmationRejected("invalid"))
        self.assertFalse(hasattr(rejected, "__cause__"))
        serialized = repr(rejected)
        for canary in (
            "本文canary",
            "表示名canary",
            "U0123456789abcdef",
            "receipt-capability-canary",
            "confirmation-token-canary",
        ):
            self.assertNotIn(canary, serialized)

    # テストケース: 実際のverify経路でverifiedと全拒否理由を生成する
    # 期待値: nested fieldまで公開契約と完全一致し、入力canaryや余剰fieldを含めない
    def test_public_results_serialize_without_sensitive_values(self):
        message = format_message("件名canary", "本文canary")
        snapshot = replace(
            self._snapshot(receipt_requested=True),
            message_fingerprint=message.fingerprint,
        )
        issued = self.service.issue(snapshot)
        verified = self.service.verify(issued.token, snapshot)
        invalid = self.service.verify(
            f"{issued.token}tampered",
            snapshot,
        )
        mismatch = self.service.verify(
            issued.token,
            replace(
                snapshot,
                target_revision=TargetRevision("e" * 64),
            ),
        )
        self.clock.current = NOW + timedelta(
            minutes=10,
            microseconds=1,
        )
        expired = self.service.verify(issued.token, snapshot)

        self.assertEqual(
            asdict(verified),
            {
                "snapshot": {
                    "owner": {"slot": 7},
                    "owner_identity": {
                        "public_id": UUID(
                            "11111111-1111-4111-8111-111111111111"
                        ),
                    },
                    "channel_public_id": UUID(
                        "22222222-2222-4222-8222-222222222222"
                    ),
                    "recipient_public_id": UUID(
                        "33333333-3333-4333-8333-333333333333"
                    ),
                    "target_revision": {"digest": "a" * 64},
                    "message_fingerprint": message.fingerprint,
                    "receipt_requested": True,
                    "receipt_expires_at": NOW + timedelta(hours=24),
                },
                "status": "verified",
            },
        )
        actual_rejections = {
            "invalid": invalid,
            "expired": expired,
            "mismatch": mismatch,
        }
        for reason, rejected in actual_rejections.items():
            with self.subTest(reason=reason):
                self.assertEqual(
                    asdict(rejected),
                    {
                        "reason": reason,
                        "status": "rejected",
                    },
                )

        for result in (verified, *actual_rejections.values()):
            with self.subTest(result=result):
                serialized = json.dumps(
                    asdict(result),
                    default=str,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for canary in (
                    "本文canary",
                    "件名canary",
                    "表示名canary",
                    "U0123456789abcdef",
                    "receipt-capability-canary",
                ):
                    self.assertNotIn(canary, serialized)

    # テストケース: PII・subject・capabilityを余剰fieldとして持つ署名済みpayloadを検証する
    # 期待値: actual verify経路はpayload内容を公開せずexactなmismatch resultだけを返す
    def test_verify_does_not_echo_sensitive_extra_payload_fields(self):
        snapshot = self._snapshot(receipt_requested=True)
        sensitive_values = (
            "件名canary",
            "本文canary",
            "表示名canary",
            "U0123456789abcdef",
            "receipt-capability-canary",
        )
        sensitive_payload = self.service.decode_for_test(
            self.service.issue(snapshot).token
        )
        sensitive_payload.update(
            {
                "subject": sensitive_values[0],
                "body": sensitive_values[1],
                "recipient_display_name": sensitive_values[2],
                "line_subject": sensitive_values[3],
                "receipt_capability": sensitive_values[4],
            }
        )
        token = self.service._signer.sign_object(
            sensitive_payload,
            compress=True,
        )

        rejected = self.service.verify(token, snapshot)

        self.assertEqual(
            asdict(rejected),
            {
                "reason": "mismatch",
                "status": "rejected",
            },
        )
        serialized = repr(rejected)
        for canary in sensitive_values:
            self.assertNotIn(canary, serialized)

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
