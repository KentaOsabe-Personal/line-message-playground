import hashlib
import json
import logging
import pickle
from base64 import urlsafe_b64encode
from dataclasses import asdict, fields
from datetime import datetime, timezone
from io import StringIO

from django.test import SimpleTestCase

from delivery.receipt import ReceiptCapabilityFactory


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
