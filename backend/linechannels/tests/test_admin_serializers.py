from datetime import datetime, timezone
from uuid import uuid4

from django.test import SimpleTestCase

from linechannels.admin_serializers import (
    ConnectionCheckRequestSerializer,
    CreateChannelRequestSerializer,
    DeleteChannelRequestSerializer,
    SetChannelStateRequestSerializer,
    UpdateChannelRequestSerializer,
)


class AdminSerializerTests(SimpleTestCase):
    def valid_create(self):
        return {
            "label": "通知チャネル",
            "messagingApiChannelId": "1234567890",
            "botUserId": "U" + "a" * 32,
            "providerId": "0012345678",
            "accessToken": "access-canary",
            "channelSecret": "secret-canary",
            "active": True,
        }

    # テストケース: 完全な登録requestとwrite-only資格情報を検証・表現する
    # 期待値: commandには秘密pairが渡るがserialized representationへ秘密fieldが現れない
    def test_create_builds_command_without_rendering_credentials(self):
        serializer = CreateChannelRequestSerializer(data=self.valid_create())

        self.assertTrue(serializer.is_valid(), serializer.errors)
        command = serializer.to_command()
        self.assertEqual(command.label, "通知チャネル")
        self.assertEqual(command.credentials.access_token.reveal_for_use(), "access-canary")
        self.assertNotIn("accessToken", serializer.data)
        self.assertNotIn("channelSecret", serializer.data)
        self.assertNotIn("canary", repr(command))

    # テストケース: unknown key、境界外label/ID/bot ID、16KiB超過秘密を登録へ渡す
    # 期待値: 対象fieldだけを示すvalidation errorとなり入力値はerrorへ含まれない
    def test_create_rejects_unknown_and_invalid_boundaries_without_echoing_values(self):
        cases = (
            ({**self.valid_create(), "ownerId": "owner-canary"}, "ownerId"),
            ({**self.valid_create(), "label": " "}, "label"),
            ({**self.valid_create(), "messagingApiChannelId": "12x"}, "messagingApiChannelId"),
            ({**self.valid_create(), "botUserId": "U" + "G" * 32}, "botUserId"),
            ({**self.valid_create(), "accessToken": "あ" * 6000}, "accessToken"),
        )
        for body, field in cases:
            with self.subTest(field=field):
                serializer = CreateChannelRequestSerializer(data=body)
                self.assertFalse(serializer.is_valid())
                self.assertIn(field, serializer.errors)
                if str(body[field]).strip():
                    self.assertNotIn(str(body[field]), str(serializer.errors))

    # テストケース: update/stateで資格情報pairを欠落、空欄、片側、新pairとして入力する
    # 期待値: 欠落・両空欄は維持、両非空は置換、片側だけはcredentialPair errorになる
    def test_optional_credential_pair_has_exact_update_semantics(self):
        revision = datetime(2026, 8, 1, tzinfo=timezone.utc).isoformat()
        base = {"expectedUpdatedAt": revision, "label": "更新後"}
        for extras, has_pair in (({}, False), ({"accessToken": "", "channelSecret": ""}, False), ({"accessToken": "new-a", "channelSecret": "new-s"}, True)):
            with self.subTest(extras=tuple(extras)):
                serializer = UpdateChannelRequestSerializer(data={**base, **extras})
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertEqual(serializer.credential_pair() is not None, has_pair)

        invalid = UpdateChannelRequestSerializer(data={**base, "accessToken": "one-sided"})
        self.assertFalse(invalid.is_valid())
        self.assertEqual(set(invalid.errors), {"credentialPair"})
        self.assertNotIn("one-sided", str(invalid.errors))

    # テストケース: canonical UUID、aware revision、state/delete/checkのexact shapeを検証する
    # 期待値: canonicalかつawareな入力だけを受理し、naive日時や余分なfieldを拒否する
    def test_operation_serializers_enforce_exact_shapes(self):
        channel_id = uuid4()
        revision = "2026-08-01T12:00:00+09:00"
        state = SetChannelStateRequestSerializer(data={"expectedUpdatedAt": revision, "active": False})
        delete = DeleteChannelRequestSerializer(data={"expectedUpdatedAt": revision})
        check = ConnectionCheckRequestSerializer(data={})
        self.assertTrue(state.is_valid(), state.errors)
        self.assertTrue(delete.is_valid(), delete.errors)
        self.assertTrue(check.is_valid(), check.errors)

        naive = DeleteChannelRequestSerializer(data={"expectedUpdatedAt": "2026-08-01T12:00:00"})
        extra = ConnectionCheckRequestSerializer(data={"channelId": str(channel_id)})
        noncanonical = UpdateChannelRequestSerializer(
            data={"expectedUpdatedAt": revision, "label": "x"},
            context={"channel_id": str(channel_id).upper()},
        )
        self.assertFalse(naive.is_valid())
        self.assertFalse(extra.is_valid())
        self.assertFalse(noncanonical.is_valid())
