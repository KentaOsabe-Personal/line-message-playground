import uuid
from datetime import timedelta
from typing import get_args

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from delivery.models import DeliveryAttempt
from delivery.types import DeliveryFailureType


class DeliveryAttemptSchemaTests(TestCase):
    def make_attempt(self, **overrides):
        accepted_at = timezone.now()
        values = {
            "operation_id": uuid.uuid4(),
            "subject": "件名",
            "body": "本文",
            "formatted_text": "【件名】\n\n本文",
            "content_fingerprint": "a" * 64,
            "active_content_fingerprint": "a" * 64,
            "request_fingerprint": "a" * 64,
            "owner_principal_slot": 1,
            "accepted_at": accepted_at,
            "processing_expires_at": accepted_at + timedelta(seconds=30),
        }
        values.update(overrides)
        return DeliveryAttempt.objects.create(**values)

    def linked_values(self):
        return {
            "target_mode": DeliveryAttempt.TargetMode.LINKED_RECIPIENT,
            "owner_principal_slot": 1,
            "owner_identity_public_id": uuid.uuid4(),
            "channel_public_id": uuid.uuid4(),
            "channel_label_snapshot": "学習用チャネル",
            "recipient_public_id": uuid.uuid4(),
            "channel_active_snapshot": True,
            "recipient_enabled_snapshot": True,
            "friendship_state_snapshot": DeliveryAttempt.FriendshipState.FRIEND,
            "request_fingerprint": "b" * 64,
            "active_content_fingerprint": None,
            "active_request_fingerprint": "b" * 64,
        }

    # テストケース: 既存fixed形式と新規linked recipient形式の処理中行を同じtableへ保存する。
    # 期待値: legacy列を持つfixed行とowner・target snapshotを持つlinked行がどちらも有効になる。
    def test_fixed_and_linked_processing_rows_are_both_valid(self):
        fixed_attempt = self.make_attempt()
        linked_attempt = self.make_attempt(
            content_fingerprint="b" * 64,
            **self.linked_values(),
        )

        self.assertEqual(fixed_attempt.target_mode, DeliveryAttempt.TargetMode.FIXED_USER)
        self.assertEqual(
            linked_attempt.target_mode,
            DeliveryAttempt.TargetMode.LINKED_RECIPIENT,
        )

    # テストケース: linked recipient形式からownerまたはtarget snapshotを一つずつ欠落させる。
    # 期待値: 各不完全なlinked行がDBのtarget整合性制約で拒否される。
    def test_linked_row_requires_owner_and_complete_target_snapshot(self):
        required_fields = (
            "owner_principal_slot",
            "request_fingerprint",
            "owner_identity_public_id",
            "channel_public_id",
            "channel_label_snapshot",
            "recipient_public_id",
            "channel_active_snapshot",
            "recipient_enabled_snapshot",
            "friendship_state_snapshot",
        )

        for index, field_name in enumerate(required_fields):
            with self.subTest(field_name=field_name):
                values = self.linked_values()
                values[field_name] = None
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.make_attempt(
                        operation_id=uuid.uuid4(),
                        content_fingerprint=f"{index + 1:064x}",
                        active_request_fingerprint=f"{index + 10:064x}",
                        **{
                            key: value
                            for key, value in values.items()
                            if key != "active_request_fingerprint"
                        },
                    )

    # テストケース: linked recipient行をLINE受付済みの終端状態として保存する。
    # 期待値: request identityとtarget snapshotは残り、active fingerprintだけが解放される。
    def test_linked_terminal_row_preserves_request_and_target_snapshot(self):
        completed_at = timezone.now()
        values = self.linked_values()
        values.update(
            active_request_fingerprint=None,
            status=DeliveryAttempt.Status.SUCCEEDED,
            sent_at=completed_at,
            completed_at=completed_at,
            line_request_id="request-id",
        )

        attempt = self.make_attempt(content_fingerprint="c" * 64, **values)

        self.assertEqual(attempt.request_fingerprint, "b" * 64)
        self.assertIsNone(attempt.active_request_fingerprint)
        self.assertIsNotNone(attempt.recipient_public_id)

    # テストケース: processing以外の状態へactive request fingerprintを保存する。
    # 期待値: active request fingerprintはprocessing行だけに許可され、終端行ではDBが拒否する。
    def test_active_request_fingerprint_is_allowed_only_while_processing(self):
        values = self.linked_values()
        values.update(
            status=DeliveryAttempt.Status.SUCCEEDED,
            sent_at=timezone.now(),
            completed_at=timezone.now(),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_attempt(content_fingerprint="c" * 64, **values)

    # テストケース: 同じactive request fingerprintを持つlinked処理中行を二件保存する。
    # 期待値: 一意制約により二件目が拒否され、同一requestの並行処理が一件へ制限される。
    def test_active_request_fingerprint_is_unique(self):
        values = self.linked_values()
        self.make_attempt(content_fingerprint="d" * 64, **values)

        values["operation_id"] = uuid.uuid4()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_attempt(content_fingerprint="e" * 64, **values)

    # テストケース: linked処理中行のactive request fingerprintを永続request identityと異なる値にする。
    # 期待値: DB制約が不一致を拒否し、処理中の一意キーが保存済みrequestと同一になる。
    def test_active_request_fingerprint_matches_persistent_request_identity(self):
        values = self.linked_values()
        values["active_request_fingerprint"] = "c" * 64

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_attempt(content_fingerprint="0" * 64, **values)

    # テストケース: receipt未要求・要求中・確認済みの各列組合せを保存する。
    # 期待値: 有効な三状態だけが同じdelivery statusと独立して保存できる。
    def test_valid_receipt_field_combinations_are_saved(self):
        now = timezone.now()
        without_receipt = self.make_attempt(content_fingerprint="f" * 64)
        pending = self.make_attempt(
            content_fingerprint="1" * 64,
            active_content_fingerprint="1" * 64,
            receipt_requested=True,
            receipt_expires_at=now + timedelta(hours=24),
            receipt_token_digest="2" * 64,
        )
        confirmed = self.make_attempt(
            content_fingerprint="3" * 64,
            active_content_fingerprint="3" * 64,
            receipt_requested=True,
            receipt_expires_at=now + timedelta(hours=24),
            receipt_token_digest="4" * 64,
            receipt_confirmed_at=now,
            receipt_webhook_event_id="01J00000000000000000000000",
        )

        self.assertFalse(without_receipt.receipt_requested)
        self.assertIsNone(pending.receipt_confirmed_at)
        self.assertEqual(confirmed.receipt_confirmed_at, now)
        self.assertEqual(confirmed.status, DeliveryAttempt.Status.PROCESSING)

    # テストケース: receipt列をrequested、commitment、確認結果の各境界で不完全に保存する。
    # 期待値: false時の付随列、true時のcommitment欠落、確認日時とevent IDの片側だけがDBで拒否される。
    def test_database_rejects_invalid_receipt_field_combinations(self):
        now = timezone.now()
        invalid_overrides = (
            {"receipt_requested": False, "receipt_expires_at": now},
            {"receipt_requested": True},
            {
                "receipt_requested": True,
                "receipt_expires_at": now + timedelta(hours=24),
            },
            {
                "receipt_requested": True,
                "receipt_token_digest": "5" * 64,
            },
            {
                "receipt_requested": True,
                "receipt_expires_at": now + timedelta(hours=24),
                "receipt_token_digest": "6" * 64,
                "receipt_confirmed_at": now,
            },
            {
                "receipt_requested": True,
                "receipt_expires_at": now + timedelta(hours=24),
                "receipt_token_digest": "7" * 64,
                "receipt_webhook_event_id": "01J00000000000000000000000",
            },
        )

        for index, overrides in enumerate(invalid_overrides):
            with self.subTest(index=index):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.make_attempt(
                        operation_id=uuid.uuid4(),
                        content_fingerprint=f"{index + 20:064x}",
                        active_content_fingerprint=f"{index + 20:064x}",
                        **overrides,
                    )

    # テストケース: 同じreceipt token digestを異なる配信へ保存する。
    # 期待値: digestの一意制約により二件目が拒否され、一つのcapabilityが一件へだけ対応する。
    def test_receipt_token_digest_is_unique(self):
        expires_at = timezone.now() + timedelta(hours=24)
        self.make_attempt(
            content_fingerprint="8" * 64,
            active_content_fingerprint="8" * 64,
            receipt_requested=True,
            receipt_expires_at=expires_at,
            receipt_token_digest="9" * 64,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_attempt(
                content_fingerprint="a" * 64,
                active_content_fingerprint="a" * 64,
                receipt_requested=True,
                receipt_expires_at=expires_at,
                receipt_token_digest="9" * 64,
            )

    # テストケース: owner scoped status lookup用indexをModel metadataから確認する。
    # 期待値: owner principal slotとoperation IDの複合indexが定義される。
    def test_owner_status_lookup_has_composite_index(self):
        indexed_fields = {tuple(index.fields) for index in DeliveryAttempt._meta.indexes}

        self.assertIn(("owner_principal_slot", "operation_id"), indexed_fields)

    # テストケース: task 1.1の全domain failureとlegacy failureをModelの終端遷移へ保存する。
    # 期待値: 全domain値を拒否せず、legacy service_unavailableも同値のまま永続化される。
    def test_model_failure_choices_cover_domain_and_preserve_legacy_value(self):
        domain_failures = set(get_args(DeliveryFailureType))
        model_failures = set(DeliveryAttempt.FailureType.values)

        self.assertTrue(domain_failures.issubset(model_failures))
        self.assertIn("service_unavailable", model_failures)

        for index, failure_type in enumerate(
            sorted(domain_failures | {"service_unavailable"})
        ):
            with self.subTest(failure_type=failure_type):
                attempt = self.make_attempt(
                    operation_id=uuid.uuid4(),
                    content_fingerprint=f"{index + 40:064x}",
                    active_content_fingerprint=f"{index + 40:064x}",
                )
                completed_at = timezone.now()

                attempt.mark_unsuccessful(
                    status=DeliveryAttempt.Status.UNKNOWN,
                    failure_type=failure_type,
                    completed_at=completed_at,
                )
                attempt.refresh_from_db()

                self.assertEqual(attempt.failure_type, failure_type)
