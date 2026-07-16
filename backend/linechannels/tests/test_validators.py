from uuid import uuid4

from django.test import SimpleTestCase

from linechannels.types import AccessToken, ChannelSecret
from linechannels.validators import (
    BoundaryValidationError,
    build_credential_pair,
    validate_bot_user_id,
    validate_label,
    validate_messaging_api_channel_id,
    validate_provider_id,
    validate_public_id,
)


class BoundaryValidatorTests(SimpleTestCase):
    def test_provider_id_is_opaque_ascii_digits_without_normalization(self):
        self.assertEqual(validate_provider_id("000123"), "000123")
        self.assertEqual(validate_provider_id("9" * 64), "9" * 64)

        for value in (None, "", " 123", "123 ", "1.0", "+1", "１２３", "1" * 65):
            with self.subTest(value=value), self.assertRaises(BoundaryValidationError):
                validate_provider_id(value)  # type: ignore[arg-type]

    # テストケース: LINE識別情報、名称、公開UUIDを正しい形式で渡す
    # 期待値: 正規化済みの値とUUIDが返る
    def test_accepts_and_normalizes_valid_non_secret_fields(self):
        public_id = uuid4()

        self.assertEqual(validate_messaging_api_channel_id("1234567890"), "1234567890")
        self.assertEqual(validate_bot_user_id("U" + "a" * 32), "U" + "a" * 32)
        self.assertEqual(validate_label("  学習用チャネル  "), "学習用チャネル")
        self.assertEqual(validate_public_id(str(public_id)), public_id)

    # テストケース: 不正なLINE識別情報と名称を渡す
    # 期待値: 入力値を含まない安全な検証エラーになる
    def test_rejects_invalid_non_secret_fields_without_echoing_values(self):
        invalid_values = (
            (validate_messaging_api_channel_id, "channel-id-canary"),
            (validate_bot_user_id, "bot-user-canary"),
            (validate_label, "   "),
            (validate_label, "x" * 256),
            (validate_public_id, "public-id-canary"),
        )

        for validator, value in invalid_values:
            with self.subTest(validator=validator.__name__):
                with self.assertRaises(BoundaryValidationError) as raised:
                    validator(value)
                self.assertNotIn(value, str(raised.exception))

    # テストケース: token と secret の完全な組を構築する
    # 期待値: 用途別 wrapper を持つ資格情報 pair が返る
    def test_builds_complete_credential_pair(self):
        pair = build_credential_pair("token-value", "secret-value")

        self.assertIsInstance(pair.access_token, AccessToken)
        self.assertIsInstance(pair.channel_secret, ChannelSecret)

    # テストケース: 片側不足、空白、または16KiB超の資格情報を渡す
    # 期待値: 平文を含まない安全な検証エラーとなり、pair は生成されない
    def test_rejects_incomplete_empty_or_oversized_credential_pair(self):
        cases = (
            (None, "secret"),
            ("token", None),
            ("", "secret"),
            ("token", "   "),
            ("x" * (16 * 1024 + 1), "secret"),
        )

        for token, secret in cases:
            with self.subTest(token_present=token is not None, secret_present=secret is not None):
                with self.assertRaises(BoundaryValidationError) as raised:
                    build_credential_pair(token, secret)
                self.assertEqual(str(raised.exception), "invalid_input")
