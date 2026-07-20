from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from uuid import uuid4

from django.test import SimpleTestCase

from linewebhooks.types import (
    FrozenJsonObject,
    HandlerFailed,
    HandlerSucceeded,
    IngressAccepted,
    IngressRejected,
    ReceiptCandidate,
    ReceiptDecision,
    VerifiedEventData,
    VerifiedWebhookEvent,
    VerifiedWebhookPayload,
    WebhookAuditEntry,
)


class WebhookValueContractTests(SimpleTestCase):
    # テストケース: 可変な検証前 event から検証済みデータを構築して元データを変更する
    # 期待値: 検証済みデータは再帰的に不変で元データと共有されない
    def test_verified_data_is_deeply_immutable(self) -> None:
        source = {"message": {"text": "secret"}, "items": [1, {"ok": True}]}
        frozen = FrozenJsonObject(source)
        source["message"]["text"] = "changed"  # type: ignore[index]

        with self.assertRaises(TypeError):
            frozen["new"] = "value"  # type: ignore[index]
        with self.assertRaises(TypeError):
            frozen["message"]["text"] = "value"  # type: ignore[index]
        self.assertEqual(frozen["message"]["text"], "secret")  # type: ignore[index]
        self.assertIsInstance(frozen["items"], tuple)

    # テストケース: event 内容 canary を含む検証済み payload と envelope を表示する
    # 期待値: safe 表現に event data が現れず識別 metadata だけが現れる
    def test_verified_contract_representations_hide_event_data(self) -> None:
        canary = "prohibited-message-canary"
        event_data = VerifiedEventData(
            webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            event_type="message",
            occurred_at_ms=1,
            is_redelivery=False,
            event=FrozenJsonObject({"message": canary}),
        )
        payload = VerifiedWebhookPayload(events=(event_data,))
        envelope = VerifiedWebhookEvent(
            channel_public_id=uuid4(),
            webhook_event_id=event_data.webhook_event_id,
            event_type=event_data.event_type,
            occurred_at_ms=event_data.occurred_at_ms,
            is_redelivery=event_data.is_redelivery,
            data=event_data.event,
        )

        self.assertNotIn(canary, repr(event_data))
        self.assertNotIn(canary, repr(payload))
        self.assertNotIn(canary, repr(envelope))
        self.assertIn("message", repr(envelope))

    # テストケース: 共通 contract のインスタンスへ属性を再代入する
    # 期待値: dataclass の全 value contract が属性変更を拒否する
    def test_value_contracts_are_frozen(self) -> None:
        accepted = IngressAccepted()

        with self.assertRaises(FrozenInstanceError):
            accepted.status = "rejected"  # type: ignore[misc]

    # テストケース: envelope と payload の field 定義を調べる
    # 期待値: raw body、署名、secret、destination、検証前 object、生例外の格納口がない
    def test_verified_contracts_exclude_forbidden_fields(self) -> None:
        forbidden = {
            "raw_body",
            "signature",
            "channel_secret",
            "destination",
            "unverified",
            "exception",
        }

        for contract in (VerifiedEventData, VerifiedWebhookPayload, VerifiedWebhookEvent):
            self.assertTrue(forbidden.isdisjoint(field.name for field in fields(contract)))

    # テストケース: 受付、handler、receipt の許可された結果を生成する
    # 期待値: 各結果は設計で定めた分類を型付き status として保持する
    def test_result_contracts_expose_only_allowed_classifications(self) -> None:
        channel_public_id = uuid4()
        candidate = ReceiptCandidate(
            channel_public_id=channel_public_id,
            webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            event_type="message",
            occurred_at_ms=1,
            is_redelivery=False,
            initial_status="processing",
        )
        decision = ReceiptDecision(
            receipt_id=1,
            webhook_event_id=candidate.webhook_event_id,
            status="unsupported",
            created=True,
        )

        self.assertEqual(IngressAccepted().status, "accepted")
        self.assertEqual(IngressRejected("payload_rejected").status, "rejected")
        self.assertEqual(HandlerSucceeded().status, "succeeded")
        self.assertEqual(HandlerFailed().status, "failed")
        self.assertEqual(candidate.initial_status, "processing")
        self.assertEqual(decision.status, "unsupported")

    # テストケース: 通常 outcome と deadline outcome の監査 entry を生成する
    # 期待値: elapsed milliseconds は deadline の非負整数にだけ許可される
    def test_audit_elapsed_time_is_restricted_to_deadline(self) -> None:
        observed_at = datetime.now(UTC)

        with self.assertRaises(ValueError):
            WebhookAuditEntry(
                outcome="event_accepted",
                observed_at=observed_at,
                elapsed_ms=1,
            )
        with self.assertRaises(ValueError):
            WebhookAuditEntry(
                outcome="response_deadline_exceeded",
                observed_at=observed_at,
                elapsed_ms=-1,
            )

        entry = WebhookAuditEntry(
            outcome="response_deadline_exceeded",
            observed_at=observed_at,
            elapsed_ms=2000,
        )
        self.assertEqual(entry.elapsed_ms, 2000)
