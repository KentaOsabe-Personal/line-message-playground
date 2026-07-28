import hashlib
import json
import logging
import pickle
from base64 import urlsafe_b64encode
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
from io import StringIO
from uuid import uuid4

from django.test import SimpleTestCase

from delivery.receipt import ReceiptCapabilityFactory, ReceiptHandler
from delivery.types import (
    DeliverySnapshot,
    LinkedTargetSnapshot,
    MessageSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
    ReceiptRecorded,
    ReceiptRejected,
    ReceiptUnchanged,
)
from lineinteractions.types import (
    ActionFailed,
    ActionNoChange,
    ActionRejected,
    ActionSucceeded,
    OpaqueActionPayload,
    PostbackActionCommand,
    VerifiedInteractionChannel,
    VerifiedInteractionUser,
)
from linewebhooks.types import HandlerExecutionContext


RECEIPT_EXPIRY = datetime(
    2026,
    7,
    27,
    1,
    2,
    3,
    456789,
    tzinfo=timezone.utc,
)


class ReceiptCapabilityFactoryTests(SimpleTestCase):
    # テストケース:
    # 確認時に確定した期限と256-bit乱数から候補を生成する
    # 期待値: wire値のSHA-256 digestと同一の期限だけがcommitmentに残る
    def test_create_binds_256_bit_capability_to_confirmed_expiry(self):
        entropy = bytes(range(32))
        raw = urlsafe_b64encode(entropy).rstrip(b"=").decode("ascii")
        factory = ReceiptCapabilityFactory(random_bytes=lambda size: entropy)

        candidate = factory.create(RECEIPT_EXPIRY)

        self.assertEqual(len(raw), 43)
        self.assertEqual(
            candidate.capability.reveal_for_push_action(),
            raw,
        )
        self.assertEqual(
            candidate.commitment.digest,
            hashlib.sha256(raw.encode("ascii")).hexdigest(),
        )
        self.assertIs(candidate.commitment.expires_at, RECEIPT_EXPIRY)
        self.assertEqual(
            {field.name for field in fields(candidate.commitment)},
            {"digest", "expires_at"},
        )
        self.assertEqual(
            asdict(candidate.commitment),
            {
                "digest": hashlib.sha256(raw.encode("ascii")).hexdigest(),
                "expires_at": RECEIPT_EXPIRY,
            },
        )

    # テストケース:
    # capability候補を表示・logging・汎用serializationへ渡す
    # 期待値: raw値を露出せず、candidateのserializeとpickleを拒否する
    def test_candidate_raw_value_is_confined_to_gateway_reveal(self):
        entropy = b"\xff" * 32
        raw = urlsafe_b64encode(entropy).rstrip(b"=").decode("ascii")
        candidate = ReceiptCapabilityFactory(
            random_bytes=lambda size: entropy
        ).create(RECEIPT_EXPIRY)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("delivery.tests.receipt")
        logger.addHandler(handler)
        try:
            logger.warning("candidate=%r", candidate)
        finally:
            logger.removeHandler(handler)

        for rendered in (repr(candidate), str(candidate), stream.getvalue()):
            self.assertNotIn(raw, rendered)
        with self.assertRaises(TypeError):
            json.dumps(candidate)
        with self.assertRaisesRegex(TypeError, "serialization is disabled"):
            pickle.dumps(candidate)
        with self.assertRaisesRegex(TypeError, "serialization is disabled"):
            asdict(candidate)

    # テストケース:
    # 既定の暗号学的乱数生成器で候補を二回生成する
    # 期待値: それぞれ独立した256-bit由来のraw値とdigestになる
    def test_default_generator_produces_distinct_candidates(self):
        factory = ReceiptCapabilityFactory()

        first = factory.create(RECEIPT_EXPIRY)
        second = factory.create(RECEIPT_EXPIRY)

        first_raw = first.capability.reveal_for_push_action()
        second_raw = second.capability.reveal_for_push_action()
        self.assertEqual(len(first_raw), 43)
        self.assertEqual(len(second_raw), 43)
        self.assertNotEqual(first_raw, second_raw)
        self.assertNotEqual(
            first.commitment.digest,
            second.commitment.digest,
        )

    # テストケース: 注入generatorが不正な型・bit長・例外を返す
    # 期待値:
    # 生の出力や下位例外を公開せず安全な生成失敗へ閉じる
    def test_rejects_invalid_generator_output_without_disclosure(self):
        invalid_generators = (
            lambda size: "secret-output",
            lambda size: b"s" * 31,
            lambda size: b"s" * 33,
            self._raise_secret_error,
        )

        for generator in invalid_generators:
            with self.subTest(generator=generator):
                with self.assertRaisesRegex(
                    ValueError,
                    "^receipt capability generation failed$",
                ) as raised:
                    ReceiptCapabilityFactory(
                        random_bytes=generator
                    ).create(RECEIPT_EXPIRY)
                self.assertNotIn("secret", repr(raised.exception))

    @staticmethod
    def _raise_secret_error(size: int) -> bytes:
        raise RuntimeError("generator-secret-canary")


class _ReceiptRepository:
    def __init__(self, result):
        self.result = result
        self.commands = []

    def confirm_receipt(self, command):
        self.commands.append(command)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ReceiptHandlerTests(SimpleTestCase):
    def setUp(self):
        self.channel_id = uuid4()
        self.recipient_id = uuid4()
        self.now = RECEIPT_EXPIRY - timedelta(hours=1)

    # テストケース: 検証済みdelivery.received actionのopaque payloadを処理する
    # 期待値: 生値を保存せずdigest・target・event・現在時刻だけをrepositoryへ渡す
    def test_hashes_opaque_payload_and_records_verified_target(self):
        snapshot = self._snapshot()
        repository = _ReceiptRepository(ReceiptRecorded(snapshot))
        handler = ReceiptHandler(
            attempt_repository=repository,
            clock=lambda: self.now,
        )

        result = handler.handle(self._command("receipt-capability-canary"))

        self.assertIsInstance(result, ActionSucceeded)
        self.assertEqual(len(repository.commands), 1)
        stored = repository.commands[0]
        self.assertEqual(
            stored.capability_digest,
            hashlib.sha256(
                b"receipt-capability-canary"
            ).hexdigest(),
        )
        self.assertEqual(stored.channel_public_id, self.channel_id)
        self.assertEqual(stored.recipient_public_id, self.recipient_id)
        self.assertEqual(stored.occurred_at, self.now)
        self.assertEqual(
            stored.webhook_event_id,
            "01J00000000000000000000000",
        )
        self.assertNotIn(
            "receipt-capability-canary",
            repr(repository.commands),
        )

    # テストケース: repositoryの記録済み・既確認・拒否結果を受け取る
    # 期待値: action成功・変更なし・拒否へ安全に一対一で縮約する
    def test_maps_repository_results_to_action_outcomes(self):
        cases = (
            (ReceiptRecorded(self._snapshot()), ActionSucceeded),
            (ReceiptUnchanged(self._snapshot()), ActionNoChange),
            (ReceiptRejected("expired"), ActionRejected),
        )

        for repository_result, expected_type in cases:
            with self.subTest(expected_type=expected_type):
                result = ReceiptHandler(
                    attempt_repository=_ReceiptRepository(repository_result),
                    clock=lambda: self.now,
                ).handle(self._command("opaque"))
                self.assertIsInstance(result, expected_type)

    # テストケース: 別action名、不正依存結果、clock・repository例外を処理する
    # 期待値: mutationを増やさず拒否または失敗へ縮約し秘密を結果へ露出しない
    def test_rejects_wrong_action_and_contains_processing_failures(self):
        wrong_repository = _ReceiptRepository(
            RuntimeError("repository-secret-canary")
        )
        wrong = self._command("payload-secret-canary", action_name="other")
        wrong_result = ReceiptHandler(
            attempt_repository=wrong_repository,
            clock=lambda: self.now,
        ).handle(wrong)
        self.assertIsInstance(wrong_result, ActionRejected)
        self.assertEqual(wrong_repository.commands, [])

        for repository, clock in (
            (
                _ReceiptRepository(
                    RuntimeError("repository-secret-canary")
                ),
                lambda: self.now,
            ),
            (
                _ReceiptRepository(ReceiptRecorded(self._snapshot())),
                self._raise_clock_error,
            ),
            (_ReceiptRepository(object()), lambda: self.now),
        ):
            with self.subTest(repository=repository):
                result = ReceiptHandler(
                    attempt_repository=repository,
                    clock=clock,
                ).handle(self._command("payload-secret-canary"))
                self.assertIsInstance(result, ActionFailed)
                self.assertNotIn("secret-canary", repr(result))

    def _command(self, payload, *, action_name="delivery.received"):
        return PostbackActionCommand(
            action_name=action_name,
            payload=OpaqueActionPayload(payload),
            channel=VerifiedInteractionChannel(
                self.channel_id,
                "provider",
            ),
            webhook_event_id="01J00000000000000000000000",
            user=VerifiedInteractionUser(uuid4(), self.recipient_id),
            execution=HandlerExecutionContext(10.0, 0, 0, 9.0),
        )

    def _snapshot(self):
        return DeliverySnapshot(
            operation_id=uuid4(),
            owner=OwnerPrincipal(1),
            owner_identity=OwnerIdentitySnapshot(uuid4()),
            target=LinkedTargetSnapshot(
                channel_public_id=self.channel_id,
                channel_label="main",
                recipient_public_id=self.recipient_id,
                channel_active=True,
                recipient_enabled=True,
                friendship_state="friend",
            ),
            message=MessageSnapshot(
                subject="件名",
                body="本文",
                formatted_text="【件名】\n\n本文",
                fingerprint="a" * 64,
            ),
            status="succeeded",
            accepted_at=self.now - timedelta(seconds=1),
            completed_at=self.now,
            line_request_id="line-request",
            line_accepted_request_id="accepted-request",
            failure=None,
            receipt_status="confirmed",
            receipt_expires_at=RECEIPT_EXPIRY,
            receipt_confirmed_at=self.now,
            receipt_webhook_event_id="01J00000000000000000000000",
        )

    @staticmethod
    def _raise_clock_error():
        raise RuntimeError("clock-secret-canary")
