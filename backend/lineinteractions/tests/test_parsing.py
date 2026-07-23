from django.test import SimpleTestCase

from lineinteractions.parsing import DefaultInteractionParser
from lineinteractions.types import (
    InvalidInteraction,
    OutOfScopeInteraction,
    ParsedPostbackInteraction,
    ParsedTextInteraction,
)

from .support import REPLY_TOKEN, SUBJECT, interaction_event


class InteractionParserTests(SimpleTestCase):
    def setUp(self):
        self.parser = DefaultInteractionParser()

    # テストケース: 有効なtext messageと未知追加fieldを解析する
    # 期待値: 入力を補正せずtext interactionとして保持する
    def test_parses_valid_text_and_ignores_unknown_fields(self):
        result = self.parser.parse(
            interaction_event(
                message={"type": "text", "text": " /Ping"},
                extra={"futureField": {"ignored": True}},
            )
        )

        self.assertIsInstance(result, ParsedTextInteraction)
        self.assertEqual(result.candidate, " /Ping")
        self.assertEqual(result.subject.reveal_for_identity_binding(), SUBJECT)
        self.assertEqual(result.reply_token.reveal_for_reply(), REPLY_TOKEN)

    # テストケース: group/room sourceを解析する
    # 期待値: 不正入力とは別のout_of_scopeへ分類する
    def test_classifies_group_and_room_as_out_of_scope(self):
        for source_type in ("group", "room"):
            with self.subTest(source_type=source_type):
                result = self.parser.parse(
                    interaction_event(source={"type": source_type})
                )
                self.assertIsInstance(result, OutOfScopeInteraction)

    # テストケース: event/source/user/reply token/message shapeの不正値を解析する
    # 期待値: すべてinvalidへ縮約する
    def test_rejects_invalid_common_and_message_shapes(self):
        cases = (
            interaction_event(event_type="follow"),
            interaction_event(source={"type": "user"}),
            interaction_event(source={"type": "user", "userId": "invalid"}),
            interaction_event(reply_token=""),
            interaction_event(reply_token="x" * 513),
            interaction_event(reply_token="😀" * 256 + "x"),
            interaction_event(reply_token="\ud800"),
            interaction_event(message={"type": "image", "text": "/ping"}),
            interaction_event(message={"type": "text"}),
            interaction_event(message={"type": "text", "text": ""}),
            interaction_event(message={"type": "text", "text": "x" * 5001}),
        )

        for event in cases:
            with self.subTest(event=event):
                self.assertIsInstance(self.parser.parse(event), InvalidInteraction)

    # テストケース: 補助平面文字・lone surrogate・UTF-16境界のtextを解析する
    # 期待値: code unit 5000以下だけを受理しencode不能値をinvalidにする
    def test_validates_text_by_utf16_code_units(self):
        accepted = ("x", "😀" * 2500, "x" * 4998 + "😀")
        rejected = ("😀" * 2500 + "x", "\ud800")

        for text in accepted:
            with self.subTest(text_length=len(text)):
                self.assertIsInstance(
                    self.parser.parse(
                        interaction_event(message={"type": "text", "text": text})
                    ),
                    ParsedTextInteraction,
                )
        for text in rejected:
            with self.subTest(text_length=len(text)):
                self.assertIsInstance(
                    self.parser.parse(
                        interaction_event(message={"type": "text", "text": text})
                    ),
                    InvalidInteraction,
                )

    # テストケース: versioned postback envelopeを最初の2区切りで解析する
    # 期待値: safe action名とdecodeしないpayloadを保持する
    def test_parses_postback_envelope_without_decoding_payload(self):
        result = self.parser.parse(
            interaction_event(
                event_type="postback",
                postback={"data": "v1:confirm:a%3Ab:c"},
            )
        )

        self.assertIsInstance(result, ParsedPostbackInteraction)
        self.assertEqual(result.action_name, "confirm")
        self.assertEqual(result.payload.reveal_for_action(), "a%3Ab:c")

        empty_payload = self.parser.parse(
            interaction_event(
                event_type="postback",
                postback={"data": "v1:a:"},
            )
        )
        self.assertIsInstance(empty_payload, ParsedPostbackInteraction)
        self.assertEqual(empty_payload.payload.reveal_for_action(), "")

    # テストケース: 不正version/action/data長のpostbackを解析する
    # 期待値: normalizeや補正をせずinvalidへ分類する
    def test_rejects_malformed_postback_data(self):
        cases = (
            "",
            "x",
            "v2:confirm:value",
            "v1:Confirm:value",
            "v1:bad action:value",
            "v1:a",
            "x" * 301,
            "\ud800",
            f"v1:{'a' * 65}:value",
        )

        for data in cases:
            with self.subTest(data_length=len(data)):
                self.assertIsInstance(
                    self.parser.parse(
                        interaction_event(
                            event_type="postback",
                            postback={"data": data},
                        )
                    ),
                    InvalidInteraction,
                )

    # テストケース: UTF-16で300 code unit境界のpostbackを解析する
    # 期待値: 300は受理し301はinvalidにする
    def test_validates_postback_by_utf16_code_units(self):
        accepted = "v1:a:" + "😀" * 147 + "x"
        rejected = accepted + "x"

        self.assertEqual(len(accepted.encode("utf-16-le")) // 2, 300)
        self.assertEqual(len(rejected.encode("utf-16-le")) // 2, 301)

        self.assertIsInstance(
            self.parser.parse(
                interaction_event(
                    event_type="postback", postback={"data": accepted}
                )
            ),
            ParsedPostbackInteraction,
        )
        self.assertIsInstance(
            self.parser.parse(
                interaction_event(
                    event_type="postback", postback={"data": rejected}
                )
            ),
            InvalidInteraction,
        )

    # テストケース: reply tokenとaction名のUTF-16/identifier境界を解析する
    # 期待値: token 1/512 code unitとaction名64文字を受理し超過値を拒否する
    def test_validates_reply_token_and_action_name_boundaries(self):
        for token in ("x", "😀" * 256):
            with self.subTest(token_length=len(token)):
                self.assertIsInstance(
                    self.parser.parse(interaction_event(reply_token=token)),
                    ParsedTextInteraction,
                )

        action_name = "a" + "0" * 63
        self.assertIsInstance(
            self.parser.parse(
                interaction_event(
                    event_type="postback",
                    postback={"data": f"v1:{action_name}:"},
                )
            ),
            ParsedPostbackInteraction,
        )

    # テストケース: parsed message/postbackをreprへ変換する
    # 期待値: subject、reply token、candidate、payloadの生値を露出しない
    def test_parsed_interactions_have_safe_repr(self):
        text_result = self.parser.parse(interaction_event())
        postback_result = self.parser.parse(interaction_event(event_type="postback"))

        self.assertIsInstance(text_result, ParsedTextInteraction)
        self.assertIsInstance(postback_result, ParsedPostbackInteraction)
        for result in (text_result, postback_result):
            rendered = repr(result)
            self.assertNotIn(SUBJECT, rendered)
            self.assertNotIn(REPLY_TOKEN, rendered)
            self.assertNotIn("/ping", rendered)
            self.assertNotIn("opaque", rendered)
