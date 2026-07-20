import json
import pickle

from django.test import SimpleTestCase

from lineaccounts.types import (
    ChannelAccessToken,
    IdToken,
    LineSubject,
    UserAccessToken,
)


class CredentialBoundaryTests(SimpleTestCase):
    # テストケース: LINE credentialと本人識別情報を表示する
    # 期待値: raw canaryがstr・repr・例外出力へ現れない
    def test_sensitive_values_are_redacted_from_display_and_errors(self):
        canary = "line-sensitive-canary"

        for value in (
            IdToken(canary),
            UserAccessToken(canary),
            ChannelAccessToken(canary),
            LineSubject(canary),
        ):
            with self.subTest(value_type=type(value).__name__):
                self.assertNotIn(canary, str(value))
                self.assertNotIn(canary, repr(value))
                self.assertNotIn(canary, str(ValueError(value)))

    # テストケース: sensitive valueを変更または汎用serializationする
    # 期待値: immutableで、vars・JSON・pickle serializationを拒否する
    def test_sensitive_values_are_immutable_and_not_serializable(self):
        value = IdToken("id-token-canary")

        with self.assertRaises(AttributeError):
            value.raw = "replacement"
        with self.assertRaises(TypeError):
            vars(value)
        with self.assertRaises(TypeError):
            json.dumps(value)
        with self.assertRaisesRegex(TypeError, "serialization is disabled"):
            pickle.dumps(value)

    # テストケース: remote call専用境界でcredential値を取り出す
    # 期待値: 明示的なreveal_for_remote_callだけが元の値を返す
    def test_credentials_reveal_only_for_remote_call(self):
        token = UserAccessToken("user-access-token-canary")

        self.assertEqual(
            token.reveal_for_remote_call(), "user-access-token-canary"
        )
