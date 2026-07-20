import base64
import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from linechannels.types import ChannelSecret
from linewebhooks.types import PayloadRejected, VerifiedWebhookPayload
from linewebhooks.verification import RawSignatureVerifier, WebhookPayloadValidator


class RawSignatureVerifierTests(SimpleTestCase):
    def setUp(self) -> None:
        self.verifier = RawSignatureVerifier()
        self.secret = ChannelSecret("channel-secret")
        self.raw_body = b'{"events":[]}'

    def _signature(self, body: bytes, secret: bytes = b"channel-secret") -> str:
        digest = hmac.new(secret, body, hashlib.sha256).digest()
        return base64.b64encode(digest).decode("ascii")

    # テストケース: 受信した raw bytes と対象チャネルのシークレットに対応する署名を検証する
    # 期待値: raw bytes が完全一致する正当な署名だけが verified になる
    def test_accepts_signature_for_exact_raw_bytes(self) -> None:
        result = self.verifier.verify(
            self.raw_body,
            self._signature(self.raw_body),
            self.secret,
        )

        self.assertEqual(result, "verified")

    # テストケース: 正当署名の生成後に本文を一 byte 変更して検証する
    # 期待値: JSON として等価でも raw bytes が異なる本文は rejected になる
    def test_rejects_one_byte_body_change(self) -> None:
        signature = self._signature(self.raw_body)

        result = self.verifier.verify(self.raw_body + b" ", signature, self.secret)

        self.assertEqual(result, "rejected")

    # テストケース: 対象チャネルとは別のシークレットで生成した署名を検証する
    # 期待値: 別シークレットの署名は rejected になる
    def test_rejects_signature_from_different_secret(self) -> None:
        result = self.verifier.verify(
            self.raw_body,
            self._signature(self.raw_body, b"different-secret"),
            self.secret,
        )

        self.assertEqual(result, "rejected")

    # テストケース: 欠落または厳密 Base64 ではない署名を検証する
    # 期待値: すべて同じ安全な rejected 分類になり例外を送出しない
    def test_rejects_missing_and_malformed_signatures(self) -> None:
        malformed_signatures = (None, "", "not-base64!", "YWJjZA")

        for signature in malformed_signatures:
            with self.subTest(signature=signature):
                self.assertEqual(
                    self.verifier.verify(self.raw_body, signature, self.secret),
                    "rejected",
                )


class WebhookPayloadValidatorTests(SimpleTestCase):
    bot_user_id = "U0123456789abcdef0123456789abcdef"

    def setUp(self) -> None:
        self.validator = WebhookPayloadValidator()

    def _event(self, index: int = 0) -> dict[str, object]:
        return {
            "webhookEventId": f"01ARZ3NDEKTSV4RRFFQ69G5FA{index:X}",
            "type": "message",
            "timestamp": index,
            "deliveryContext": {"isRedelivery": False},
        }

    def _body(
        self,
        *,
        destination: object | None = None,
        events: object | None = None,
    ) -> bytes:
        payload = {
            "destination": self.bot_user_id if destination is None else destination,
            "events": [] if events is None else events,
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    # テストケース: 署名済み payload の raw body が256 KiBちょうどと1 byte超過の場合を検証する
    # 期待値: 上限ちょうどだけを受理し、超過はJSON解析結果にかかわらず rejected になる
    def test_enforces_raw_body_size_limit_before_parsing(self) -> None:
        base = self._body()
        at_limit = base + (b" " * ((256 * 1024) - len(base)))

        accepted = self.validator.validate(at_limit, self.bot_user_id)
        rejected = self.validator.validate(at_limit + b" ", self.bot_user_id)

        self.assertIsInstance(accepted, VerifiedWebhookPayload)
        self.assertIsInstance(rejected, PayloadRejected)

    # テストケース: events が空、1件、10件、11件の payload を検証する
    # 期待値: 0〜10件を入力順で受理し、11件は request 全体を rejected にする
    def test_accepts_zero_to_ten_events_and_rejects_eleven(self) -> None:
        for count in (0, 1, 10):
            with self.subTest(count=count):
                result = self.validator.validate(
                    self._body(events=[self._event(index) for index in range(count)]),
                    self.bot_user_id,
                )
                self.assertIsInstance(result, VerifiedWebhookPayload)
                self.assertEqual(len(result.events), count)

        rejected = self.validator.validate(
            self._body(events=[self._event(index) for index in range(11)]),
            self.bot_user_id,
        )
        self.assertIsInstance(rejected, PayloadRejected)

    # テストケース: 不正JSON、非object root、events欠落または非arrayを検証する
    # 期待値: top-level基本構造を満たさない payload はすべて rejected になる
    def test_rejects_invalid_json_and_top_level_structure(self) -> None:
        invalid_bodies = (
            b"not-json",
            b"[]",
            json.dumps({"destination": self.bot_user_id}).encode(),
            self._body(events={}),
        )

        for raw_body in invalid_bodies:
            with self.subTest(raw_body=raw_body[:20]):
                self.assertIsInstance(
                    self.validator.validate(raw_body, self.bot_user_id),
                    PayloadRejected,
                )

    # テストケース: destination の欠落、型不正、または選択チャネルとの不一致を検証する
    # 期待値: destination を完全一致で確認し、不正時は request 全体を rejected にする
    def test_rejects_missing_invalid_or_mismatched_destination(self) -> None:
        invalid_payloads = (
            {"events": []},
            {"destination": 1, "events": []},
            {"destination": "Udifferent", "events": []},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                raw_body = json.dumps(payload, separators=(",", ":")).encode()
                self.assertIsInstance(
                    self.validator.validate(raw_body, self.bot_user_id),
                    PayloadRejected,
                )

    # テストケース: event共通属性へ仕様上の有効な最小値・最大値・真偽値を設定する
    # 期待値: canonical ULID、type 1〜255文字、timestamp 0〜signed 64-bit最大、再送真偽を受理する
    def test_accepts_common_event_attribute_boundaries(self) -> None:
        valid_events = (
            {
                **self._event(),
                "webhookEventId": "00000000000000000000000000",
                "type": "x",
                "timestamp": 0,
                "deliveryContext": {"isRedelivery": False},
            },
            {
                **self._event(),
                "webhookEventId": "7ZZZZZZZZZZZZZZZZZZZZZZZZZ",
                "type": "x" * 255,
                "timestamp": (2**63) - 1,
                "deliveryContext": {"isRedelivery": True},
            },
        )

        result = self.validator.validate(
            self._body(events=list(valid_events)),
            self.bot_user_id,
        )

        self.assertIsInstance(result, VerifiedWebhookPayload)
        self.assertEqual(len(result.events), 2)
        self.assertFalse(result.events[0].is_redelivery)
        self.assertTrue(result.events[1].is_redelivery)

    # テストケース: event object と各共通必須属性の欠落・型・境界値を検証する
    # 期待値: webhookEventId、type、timestamp、再送表示のいずれかが不正なら rejected になる
    def test_rejects_invalid_common_event_attributes(self) -> None:
        valid = self._event()
        invalid_events = (
            "not-an-object",
            {**valid, "webhookEventId": "lowercase-or-short"},
            {**valid, "webhookEventId": "8ZZZZZZZZZZZZZZZZZZZZZZZZZ"},
            {**valid, "webhookEventId": "0IIIIIIIIIIIIIIIIIIIIIIIII"},
            {**valid, "webhookEventId": "01arz3ndektsv4rrffq69g5fav"},
            {**valid, "type": ""},
            {**valid, "type": "x" * 256},
            {**valid, "timestamp": True},
            {**valid, "timestamp": -1},
            {**valid, "timestamp": 2**63},
            {**valid, "deliveryContext": []},
            {**valid, "deliveryContext": {}},
            {**valid, "deliveryContext": {"isRedelivery": 1}},
        )
        for field in ("webhookEventId", "type", "timestamp", "deliveryContext"):
            invalid_events += ({key: value for key, value in valid.items() if key != field},)

        for event in invalid_events:
            with self.subTest(event=event):
                self.assertIsInstance(
                    self.validator.validate(self._body(events=[event]), self.bot_user_id),
                    PayloadRejected,
                )

    # テストケース: 一件の正常 event と一件の不正 event を同じ payload で検証する
    # 期待値: 一件でも不正なら正常 event を含めて一件も返さず request 全体を rejected にする
    def test_rejects_entire_batch_when_one_event_is_invalid(self) -> None:
        invalid = {**self._event(1), "timestamp": -1}

        result = self.validator.validate(
            self._body(events=[self._event(), invalid]),
            self.bot_user_id,
        )

        self.assertIsInstance(result, PayloadRejected)


class TolerantEventConversionTests(SimpleTestCase):
    bot_user_id = WebhookPayloadValidatorTests.bot_user_id

    def setUp(self) -> None:
        self.validator = WebhookPayloadValidator()

    def _event(self, index: int = 0) -> dict[str, object]:
        return {
            "webhookEventId": f"01ARZ3NDEKTSV4RRFFQ69G5FA{index:X}",
            "type": "message",
            "timestamp": index,
            "deliveryContext": {"isRedelivery": False},
        }

    def _body(self, *, events: object) -> bytes:
        return json.dumps(
            {"destination": self.bot_user_id, "events": events},
            separators=(",", ":"),
        ).encode("utf-8")

    # テストケース: 未知field・既知field内の未知enum・未知event typeを含む複数eventを検証する
    # 期待値: 未知要素とfield順序を保持したまま全eventを入力順のtupleとして受理する
    def test_preserves_unknown_data_and_mixed_event_order(self) -> None:
        known_event = {
            "type": "message",
            "webhookEventId": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "timestamp": 1,
            "deliveryContext": {"isRedelivery": False, "futureMode": "new"},
            "message": {"type": "future-enum", "custom": [1, 2]},
        }
        unknown_event = {
            "futureFirst": True,
            "webhookEventId": "01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "type": "future-event",
            "timestamp": 2,
            "deliveryContext": {"isRedelivery": True},
        }

        result = self.validator.validate(
            self._body(events=[known_event, unknown_event]),
            self.bot_user_id,
        )

        self.assertIsInstance(result, VerifiedWebhookPayload)
        self.assertEqual(
            tuple(result.events[0].event),
            tuple(known_event),
        )
        self.assertEqual(result.events[0].event["message"]["type"], "future-enum")  # type: ignore[index]
        self.assertEqual(result.events[1].event_type, "future-event")
        self.assertEqual(result.events[1].event["futureFirst"], True)

    # テストケース: JSON parserが返した可変objectを検証後に変更し、検証済みeventの変更も試みる
    # 期待値: 検証済みeventは元objectと共有せず、object・配列とも再帰的に変更を拒否する
    def test_detaches_and_deeply_freezes_unverified_event_object(self) -> None:
        source_event = {
            **self._event(),
            "message": {"text": "original", "items": [1, {"ok": True}]},
        }
        parsed = {"destination": self.bot_user_id, "events": [source_event]}
        with patch("linewebhooks.verification.json.loads", return_value=parsed):
            result = self.validator.validate(b"{}", self.bot_user_id)

        self.assertIsInstance(result, VerifiedWebhookPayload)
        source_event["message"]["text"] = "changed"  # type: ignore[index]
        frozen_event = result.events[0].event

        self.assertEqual(frozen_event["message"]["text"], "original")  # type: ignore[index]
        self.assertIsInstance(frozen_event["message"]["items"], tuple)  # type: ignore[index]
        with self.assertRaises(TypeError):
            frozen_event["message"]["text"] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            frozen_event["message"]["items"][1]["ok"] = False  # type: ignore[index]

    # テストケース: 検証済みpayloadへ複数eventを渡して返却containerを調べる
    # 期待値: payloadはimmutableなevent tupleだけを持ち、top-level destinationを含まない
    def test_returns_content_safe_immutable_event_tuple(self) -> None:
        result = self.validator.validate(
            self._body(events=[self._event(0), self._event(1)]),
            self.bot_user_id,
        )

        self.assertIsInstance(result, VerifiedWebhookPayload)
        self.assertIsInstance(result.events, tuple)
        self.assertEqual(len(result.events), 2)
        self.assertFalse(hasattr(result, "destination"))
