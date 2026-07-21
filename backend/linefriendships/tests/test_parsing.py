from uuid import uuid4

from django.test import SimpleTestCase

from linewebhooks.types import FrozenJsonObject, VerifiedWebhookEvent

from linefriendships.parsing import DefaultFriendshipEventParser
from linefriendships.types import (
    InvalidFriendshipEvent,
    OutOfScopeSource,
    ValidatedFriendshipEvent,
)


class FriendshipEventParserTests(SimpleTestCase):
    def setUp(self):
        self.parser = DefaultFriendshipEventParser()
        self.canary = "U" + "a" * 32

    def event(self, *, event_type="follow", data=None, is_redelivery=False):
        if data is None:
            data = {
                "type": event_type,
                "source": {"type": "user", "userId": self.canary},
            }
        return VerifiedWebhookEvent(
            channel_public_id=uuid4(),
            webhook_event_id="01J00000000000000000000000",
            event_type=event_type,
            occurred_at_ms=123,
            is_redelivery=is_redelivery,
            data=FrozenJsonObject(data),
        )

    # テストケース: user sourceのfollowにstrict booleanと未知fieldを含める
    # 期待値: LINE user IDをredacted値へ包み、friend commandへ変換する
    def test_parses_valid_follow_and_ignores_unknown_fields(self):
        event = self.event(
            data={
                "type": "follow",
                "source": {
                    "type": "user",
                    "userId": self.canary,
                    "unknown": "ignored",
                },
                "follow": {"isUnblocked": True, "unknown": 1},
                "unknown": {"nested": "ignored"},
            },
            is_redelivery=True,
        )

        result = self.parser.parse(event)

        self.assertIsInstance(result, ValidatedFriendshipEvent)
        assert isinstance(result, ValidatedFriendshipEvent)
        self.assertEqual(result.target_state, "friend")
        self.assertIs(result.is_unblocked, True)
        self.assertNotIn(self.canary, repr(result))
        self.assertNotIn(self.canary, repr(result.subject))

    # テストケース: user sourceのunfollowにfollow風metadataを含める
    # 期待値: 補助flagを読まずnot_friend commandへ変換する
    def test_parses_unfollow_without_unblock_metadata(self):
        result = self.parser.parse(
            self.event(
                event_type="unfollow",
                data={
                    "type": "unfollow",
                    "source": {"type": "user", "userId": self.canary},
                    "follow": {"isUnblocked": "not-read"},
                },
            )
        )

        self.assertIsInstance(result, ValidatedFriendshipEvent)
        assert isinstance(result, ValidatedFriendshipEvent)
        self.assertEqual(result.target_state, "not_friend")
        self.assertIsNone(result.is_unblocked)

    # テストケース: groupまたはroom sourceの友だちeventを解釈する
    # 期待値: PIIを保持しない対象外分類へ縮約する
    def test_classifies_group_and_room_as_out_of_scope(self):
        for source_type in ("group", "room"):
            with self.subTest(source_type=source_type):
                result = self.parser.parse(
                    self.event(data={"source": {"type": source_type}})
                )
                self.assertEqual(result, OutOfScopeSource())

    # テストケース: source/userIdの欠落・不正値・未知sourceを解釈する
    # 期待値: 入力値をerrorへ含めず不正分類へ縮約する
    def test_classifies_invalid_source_and_user_id(self):
        invalid_sources = (
            None,
            {},
            {"type": "unknown", "userId": self.canary},
            {"type": "user"},
            {"type": "user", "userId": "U" + "A" * 32},
            {"type": "user", "userId": "not-a-line-user-id"},
        )
        for source in invalid_sources:
            with self.subTest(source=repr(source)):
                data = {} if source is None else {"source": source}
                self.assertEqual(
                    self.parser.parse(self.event(data=data)),
                    InvalidFriendshipEvent(),
                )

    # テストケース: follow.isUnblockedへbool以外またはobject以外を渡す
    # 期待値: 0/1を含めstrict boolean以外を不正分類する
    def test_rejects_non_boolean_unblock_flag(self):
        for follow in (
            {"isUnblocked": 0},
            {"isUnblocked": 1},
            {"isUnblocked": "true"},
            [],
        ):
            with self.subTest(follow=repr(follow)):
                self.assertEqual(
                    self.parser.parse(
                        self.event(
                            data={
                                "source": {
                                    "type": "user",
                                    "userId": self.canary,
                                },
                                "follow": follow,
                            }
                        )
                    ),
                    InvalidFriendshipEvent(),
                )

    # テストケース: follow/unfollow以外の検証済みeventを直接parserへ渡す
    # 期待値: 状態根拠にせず不正分類する
    def test_rejects_unsupported_event_type(self):
        self.assertEqual(
            self.parser.parse(self.event(event_type="message")),
            InvalidFriendshipEvent(),
        )
