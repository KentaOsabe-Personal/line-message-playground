from django.test import SimpleTestCase

from lineaccounts.serializers import (
    LineLoginRequestSerializer,
    RecipientRegistrationRequestSerializer,
    RecipientStateRequestSerializer,
    UnlinkRequestSerializer,
)
from lineaccounts.types import IdToken, UserAccessToken


class StrictRequestSerializerTests(SimpleTestCase):
    # テストケース: login requestへprofileとtoken aliasを混在させる
    # 期待値: 未知fieldを値のechoなしで拒否する
    def test_login_rejects_unknown_profile_and_token_alias_fields(self):
        canary = "credential-canary"
        serializer = LineLoginRequestSerializer(
            data={
                "idToken": canary,
                "profile": {"userId": "U-secret"},
                "token": canary,
            }
        )

        self.assertFalse(serializer.is_valid())
        rendered_errors = repr(serializer.errors)
        self.assertNotIn(canary, rendered_errors)
        self.assertNotIn("U-secret", rendered_errors)
        self.assertIn("profile", serializer.errors)
        self.assertIn("token", serializer.errors)

    # テストケース: recipient登録へuser IDを手入力する
    # 期待値: userIdを拒否し、定義済みfieldだけをvalidated_dataへ渡す
    def test_recipient_registration_rejects_user_id(self):
        serializer = RecipientRegistrationRequestSerializer(
            data={
                "channelId": "019f69af-d93e-7dd2-b9d2-33f123c978ce",
                "accessToken": "access-token-canary",
                "userId": "U-secret",
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"userId"})

    # テストケース: 定義済みのrequest fieldだけを送信する
    # 期待値: strict serializerが型を保ったvalidated_dataを返す
    def test_valid_requests_expose_only_declared_fields(self):
        state = RecipientStateRequestSerializer(data={"enabled": True})
        unlink = UnlinkRequestSerializer(
            data={
                "confirmationToken": "confirmation",
                "userAccessToken": "user-token",
            }
        )

        self.assertTrue(state.is_valid(), state.errors)
        self.assertEqual(state.validated_data, {"enabled": True})
        self.assertTrue(unlink.is_valid(), unlink.errors)
        self.assertEqual(
            set(unlink.validated_data),
            {"confirmationToken", "userAccessToken"},
        )
        self.assertTrue(
            UnlinkRequestSerializer().fields["userAccessToken"].write_only
        )

    # テストケース: 有効なLINE credential requestを内部値へ変換する
    # 期待値: domain側へraw stringではなくredactedな専用型を渡す
    def test_credentials_are_converted_to_redacted_boundary_types(self):
        login = LineLoginRequestSerializer(data={"idToken": "id-token-canary"})
        recipient = RecipientRegistrationRequestSerializer(
            data={
                "channelId": "019f69af-d93e-7dd2-b9d2-33f123c978ce",
                "accessToken": "access-token-canary",
            }
        )

        self.assertTrue(login.is_valid(), login.errors)
        self.assertIsInstance(login.validated_data["idToken"], IdToken)
        self.assertNotIn("id-token-canary", repr(login.validated_data))
        self.assertTrue(recipient.is_valid(), recipient.errors)
        self.assertIsInstance(
            recipient.validated_data["accessToken"], UserAccessToken
        )
        self.assertNotIn("access-token-canary", repr(recipient.validated_data))

    # テストケース: enabledへbool以外のJSON scalarを渡す
    # 期待値: truthy文字列や数値への暗黙変換を拒否する
    def test_enabled_rejects_non_boolean_scalars(self):
        for value in ("true", 1, 0):
            with self.subTest(value=value):
                serializer = RecipientStateRequestSerializer(data={"enabled": value})
                self.assertFalse(serializer.is_valid())

    # テストケース: string credential fieldへ非string JSON値を渡す
    # 期待値: 文字列へ暗黙変換せず安全なfield errorにする
    def test_credentials_reject_non_string_scalars(self):
        serializer = LineLoginRequestSerializer(data={"idToken": 123})

        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"idToken"})
        self.assertNotIn("123", repr(serializer.errors))
