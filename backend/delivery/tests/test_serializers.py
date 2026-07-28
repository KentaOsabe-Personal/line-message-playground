from uuid import UUID

from django.test import SimpleTestCase

from delivery.serializers import (
    LinkedPreviewRequestSerializer,
    LinkedSendDeliveryRequestSerializer,
)


CHANNEL_ID = "019f69af-d93e-7dd2-b9d2-33f123c978ce"
RECIPIENT_ID = "019f69b0-0144-7a29-b907-9b7977d707d5"
OPERATION_ID = "019f69b0-64dd-748e-99dc-b105de9f26b1"


def preview_payload(**overrides):
    payload = {
        "channelId": CHANNEL_ID,
        "recipientId": RECIPIENT_ID,
        "subject": "件名",
        "body": "本文",
        "receiptRequested": True,
    }
    payload.update(overrides)
    return payload


def send_payload(**overrides):
    payload = preview_payload(
        operationId=OPERATION_ID,
        confirmationToken="opaque-confirmation",
    )
    payload.update(overrides)
    return payload


class LinkedDeliveryRequestSerializerTests(SimpleTestCase):
    # テストケース: canonicalなpreview／send DTOを検証する
    # 期待値: UUIDとbooleanの型を保ち、宣言済みfieldだけを内部値へ渡す
    def test_valid_requests_expose_only_canonical_declared_fields(self):
        preview = LinkedPreviewRequestSerializer(data=preview_payload())
        send = LinkedSendDeliveryRequestSerializer(data=send_payload())

        self.assertTrue(preview.is_valid(), preview.errors)
        self.assertEqual(
            set(preview.validated_data),
            {"channelId", "recipientId", "subject", "body", "receiptRequested"},
        )
        self.assertIsInstance(preview.validated_data["channelId"], UUID)
        self.assertIsInstance(preview.validated_data["recipientId"], UUID)
        self.assertIs(preview.validated_data["receiptRequested"], True)

        self.assertTrue(send.is_valid(), send.errors)
        self.assertEqual(
            set(send.validated_data),
            {
                "channelId",
                "recipientId",
                "subject",
                "body",
                "receiptRequested",
                "operationId",
                "confirmationToken",
            },
        )
        self.assertIsInstance(send.validated_data["operationId"], UUID)
        self.assertTrue(
            LinkedSendDeliveryRequestSerializer()
            .fields["confirmationToken"]
            .write_only
        )

    # テストケース: UUIDを非canonical形式または非string値で送る
    # 期待値: 値を補正せず、該当UUID fieldだけを安全に拒否する
    def test_uuid_fields_reject_non_canonical_or_non_string_values(self):
        invalid_cases = (
            ("channelId", CHANNEL_ID.upper()),
            ("recipientId", RECIPIENT_ID.replace("-", "")),
            ("operationId", f"{{{OPERATION_ID}}}"),
            ("operationId", UUID(OPERATION_ID)),
        )

        for field, value in invalid_cases:
            with self.subTest(field=field, value=value):
                serializer = LinkedSendDeliveryRequestSerializer(
                    data=send_payload(**{field: value})
                )

                self.assertFalse(serializer.is_valid())
                self.assertEqual(set(serializer.errors), {field})
                self.assertNotIn(str(value), repr(serializer.errors))

    # テストケース: receiptRequestedへJSON boolean以外を渡す
    # 期待値: 数値や文字列をtruthy／falseyへ暗黙変換せず拒否する
    def test_receipt_requested_rejects_non_boolean_scalars(self):
        for value in ("true", "false", 1, 0, None):
            with self.subTest(value=value):
                serializer = LinkedPreviewRequestSerializer(
                    data=preview_payload(receiptRequested=value)
                )

                self.assertFalse(serializer.is_valid())
                self.assertEqual(set(serializer.errors), {"receiptRequested"})

    # テストケース: text fieldへ非string JSON値を渡す
    # 期待値: 文字列へ暗黙変換せずfield別の安全なerrorを返す
    def test_text_fields_reject_non_string_scalars_without_echo(self):
        invalid_cases = (
            ("subject", 123),
            ("body", False),
            ("confirmationToken", {"secret": "token-canary"}),
        )

        for field, value in invalid_cases:
            with self.subTest(field=field):
                serializer = LinkedSendDeliveryRequestSerializer(
                    data=send_payload(**{field: value})
                )

                self.assertFalse(serializer.is_valid())
                self.assertEqual(set(serializer.errors), {field})
                self.assertNotIn("token-canary", repr(serializer.errors))

    # テストケース: preview／sendの必須fieldを欠落させる
    # 期待値: 欠落したfieldを特定し、requestをfail fastする
    def test_requests_reject_each_missing_required_field(self):
        for serializer_class, payload in (
            (LinkedPreviewRequestSerializer, preview_payload()),
            (LinkedSendDeliveryRequestSerializer, send_payload()),
        ):
            for field in tuple(payload):
                with self.subTest(serializer=serializer_class.__name__, field=field):
                    incomplete = dict(payload)
                    incomplete.pop(field)
                    serializer = serializer_class(data=incomplete)

                    self.assertFalse(serializer.is_valid())
                    self.assertEqual(set(serializer.errors), {field})

    # テストケース: 任意LINE user ID、owner scope、未知fieldを混入する
    # 期待値: 余剰fieldをすべて値のechoなしで明示的に拒否する
    def test_requests_reject_unknown_target_and_owner_fields_safely(self):
        serializer = LinkedPreviewRequestSerializer(
            data=preview_payload(
                userId="U-secret-canary",
                ownerId="owner-secret-canary",
                unexpected="unexpected-canary",
            )
        )

        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"non_field_errors"})
        rendered_errors = repr(serializer.errors)
        self.assertNotIn("userId", rendered_errors)
        self.assertNotIn("ownerId", rendered_errors)
        self.assertNotIn("unexpected", rendered_errors)
        self.assertNotIn("U-secret-canary", rendered_errors)
        self.assertNotIn("owner-secret-canary", rendered_errors)
        self.assertNotIn("unexpected-canary", rendered_errors)

    # テストケース: secretやcapabilityそのものを未知field名として送る
    # 期待値: 攻撃者が指定したkey名も値も固定errorへechoしない
    def test_requests_do_not_echo_secret_shaped_unknown_field_names(self):
        secret_key = "receipt-capability-secret-canary"
        serializer = LinkedSendDeliveryRequestSerializer(
            data=send_payload(**{secret_key: "credential-value-canary"})
        )

        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"non_field_errors"})
        rendered_errors = repr(serializer.errors)
        self.assertNotIn(secret_key, rendered_errors)
        self.assertNotIn("credential-value-canary", rendered_errors)

    # テストケース: JSON object以外をrequest serializerへ渡す
    # 期待値: field走査前に安全な共通errorとして拒否する
    def test_requests_reject_non_object_payload_safely(self):
        serializer = LinkedPreviewRequestSerializer(data=["secret-canary"])

        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"non_field_errors"})
        self.assertNotIn("secret-canary", repr(serializer.errors))
