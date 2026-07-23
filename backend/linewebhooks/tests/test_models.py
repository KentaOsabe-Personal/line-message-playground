from datetime import UTC, datetime
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import TestCase

from linewebhooks.models import WebhookEventReceipt


class WebhookEventReceiptTests(TestCase):
    def _values(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "webhook_event_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "channel_public_id": uuid4(),
            "event_type": "message",
            "occurred_at_ms": 1,
            "is_redelivery": False,
            "status": WebhookEventReceipt.Status.PROCESSING,
            "failure_code": None,
            "completed_at": None,
        }
        values.update(overrides)
        return values

    # テストケース: 受付台帳 model の永続 field 一覧を調べる
    # 期待値: 最小 metadata と状態だけを持ち payload や秘密情報の列を持たない
    def test_model_stores_only_minimal_receipt_metadata(self) -> None:
        field_names = {field.name for field in WebhookEventReceipt._meta.fields}

        self.assertEqual(
            field_names,
            {
                "id",
                "webhook_event_id",
                "channel_public_id",
                "event_type",
                "occurred_at_ms",
                "is_redelivery",
                "status",
                "failure_code",
                "accepted_at",
                "completed_at",
                "updated_at",
            },
        )
        self.assertTrue(
            {
                "payload",
                "signature",
                "destination",
                "user_id",
                "reply_token",
                "channel",
            }.isdisjoint(field_names)
        )

    # テストケース: 異なるチャネルで同じ webhookEventId を二度保存する
    # 期待値: webhookEventId の全体一意制約が二件目を拒否する
    def test_webhook_event_id_is_globally_unique(self) -> None:
        WebhookEventReceipt.objects.create(**self._values())

        with self.assertRaises(IntegrityError), transaction.atomic():
            WebhookEventReceipt.objects.create(
                **self._values(channel_public_id=uuid4())
            )

        self.assertEqual(WebhookEventReceipt.objects.count(), 1)

    # テストケース: processing、processed、unsupported、failed の正しい状態を保存する
    # 期待値: 各状態と完了時刻・安全な失敗分類の組合せを DB が受理する
    def test_valid_status_combinations_are_accepted(self) -> None:
        completed_at = datetime.now(UTC)
        rows = (
            self._values(),
            self._values(
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
                status=WebhookEventReceipt.Status.PROCESSED,
                completed_at=completed_at,
            ),
            self._values(
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAX",
                status=WebhookEventReceipt.Status.UNSUPPORTED,
                completed_at=completed_at,
            ),
            self._values(
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAY",
                status=WebhookEventReceipt.Status.FAILED,
                failure_code=WebhookEventReceipt.FailureCode.HANDLER_FAILED,
                completed_at=completed_at,
            ),
            self._values(
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAZ",
                status=WebhookEventReceipt.Status.FAILED,
                failure_code=(
                    WebhookEventReceipt.FailureCode.DISPATCH_DEADLINE_EXCEEDED
                ),
                completed_at=completed_at,
            ),
        )

        for values in rows:
            WebhookEventReceipt.objects.create(**values)

        self.assertEqual(WebhookEventReceipt.objects.count(), 5)

    # テストケース: status と完了時刻・失敗分類が矛盾する行を保存する
    # 期待値: 各不正な状態組合せを DB CHECK 制約が拒否する
    def test_invalid_status_combinations_are_rejected(self) -> None:
        completed_at = datetime.now(UTC)
        invalid_rows = (
            self._values(completed_at=completed_at),
            self._values(
                status=WebhookEventReceipt.Status.PROCESSED,
                completed_at=None,
            ),
            self._values(
                status=WebhookEventReceipt.Status.UNSUPPORTED,
                completed_at=completed_at,
                failure_code=WebhookEventReceipt.FailureCode.HANDLER_FAILED,
            ),
            self._values(
                status=WebhookEventReceipt.Status.FAILED,
                completed_at=completed_at,
                failure_code=None,
            ),
        )

        for index, values in enumerate(invalid_rows):
            values["webhook_event_id"] = f"01ARZ3NDEKTSV4RRFFQ69G5FA{index}"
            with self.subTest(index=index):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    WebhookEventReceipt.objects.create(**values)

    # テストケース: 負の event 発生時刻を保存する
    # 期待値: unsigned 相当の CHECK 制約が保存を拒否する
    def test_negative_occurred_at_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            WebhookEventReceipt.objects.create(**self._values(occurred_at_ms=-1))
