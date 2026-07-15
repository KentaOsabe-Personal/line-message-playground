import uuid
import getpass
import warnings

from django.test import SimpleTestCase

from linechannels.management.prompts import GetPassManageLineChannelPrompts
from linechannels.types import (
    RegisterLineChannel,
    SetLineChannelActive,
    UpdateLineChannel,
)


class TTYStream:
    def isatty(self):
        return True


class NonTTYStream:
    def isatty(self):
        return False


class PromptHarness:
    def __init__(self, visible, hidden=(), *, stream=None):
        self.visible = iter(visible)
        self.hidden = iter(hidden)
        self.visible_prompts = []
        self.hidden_prompts = []
        self.stream = stream or TTYStream()

    def input(self, prompt):
        self.visible_prompts.append(prompt)
        return next(self.visible)

    def getpass(self, prompt):
        self.hidden_prompts.append(prompt)
        return next(self.hidden)

    def prompts(self):
        return GetPassManageLineChannelPrompts(
            input_stream=self.stream,
            input_fn=self.input,
            getpass_fn=self.getpass,
        )


class GetPassManageLineChannelPromptsTests(SimpleTestCase):
    # テストケース: 対話登録で一致するtokenとsecretを確認付きで入力する
    # 期待値: 2秘密をhidden inputだけで収集し、promptや結果表現へ露出しない
    def test_register_collects_confirmed_secrets_only_through_hidden_input(self):
        harness = PromptHarness(
            ["register", "1234567890", "U" + "1" * 32, "メイン", "yes"],
            ["token-canary", "token-canary", "secret-canary", "secret-canary"],
        )

        result = harness.prompts().collect()

        self.assertEqual(result.status, "collected")
        self.assertIsInstance(result.value, RegisterLineChannel)
        self.assertEqual(len(harness.hidden_prompts), 4)
        visible_text = "".join(harness.visible_prompts)
        self.assertNotIn("token-canary", visible_text)
        self.assertNotIn("secret-canary", visible_text)
        self.assertNotIn("token-canary", repr(result))
        self.assertNotIn("secret-canary", repr(result))

    # テストケース: 資格情報を置換せず、登録済みチャネルの名称だけを更新する
    # 期待値: 既存秘密を読み戻さずhidden inputも呼ばず、指定項目だけの入力を返す
    def test_update_without_replacement_never_reads_existing_or_new_secrets(self):
        public_id = uuid.uuid4()
        harness = PromptHarness(
            ["update", str(public_id), "", "", "更新後", "no", "keep"]
        )

        result = harness.prompts().collect()

        self.assertEqual(result.status, "collected")
        self.assertIsInstance(result.value, UpdateLineChannel)
        self.assertEqual(result.value.channel_public_id, public_id)
        self.assertEqual(result.value.label, "更新後")
        self.assertIsNone(result.value.credentials)
        self.assertEqual(harness.hidden_prompts, [])

    # テストケース: 公開UUIDを指定して有効化または無効化を選択する
    # 期待値: 秘密入力なしで公開UUIDと状態だけを持つ型付き入力を返す
    def test_enable_and_disable_return_typed_public_only_input(self):
        for action, expected in (("enable", True), ("disable", False)):
            public_id = uuid.uuid4()
            harness = PromptHarness([action, str(public_id)])

            result = harness.prompts().collect()

            self.assertEqual(result.status, "collected")
            self.assertEqual(result.value, SetLineChannelActive(public_id, expected))
            self.assertEqual(harness.hidden_prompts, [])

    # テストケース: 非TTY、明示取消、または秘密の確認不一致が発生する
    # 期待値: invalidまたはcancelledへ分類し、mutation用入力を生成しない
    def test_non_tty_cancel_and_confirmation_mismatch_never_create_mutation_input(self):
        cases = (
            PromptHarness([], stream=NonTTYStream()),
            PromptHarness(["cancel"]),
            PromptHarness(
                ["register", "1234567890", "U" + "1" * 32, "メイン", "yes"],
                ["token-canary", "different-token"],
            ),
        )

        statuses = [case.prompts().collect().status for case in cases]

        self.assertEqual(statuses, ["invalid", "cancelled", "invalid"])

    # テストケース: 空更新、EOF、割込み、またはgetpassのecho fallback警告が発生する
    # 期待値: すべてinvalidへ分類し、端末入力を継続してmutation用入力を生成しない
    def test_empty_update_and_terminal_input_failures_are_invalid(self):
        public_id = uuid.uuid4()
        empty_update = PromptHarness(
            ["update", str(public_id), "", "", "", "no", "keep"]
        ).prompts()

        def fail_with(error):
            def failing_input(_prompt):
                raise error

            return GetPassManageLineChannelPrompts(
                input_stream=TTYStream(), input_fn=failing_input
            )

        warning_harness = PromptHarness(
            ["register", "1234567890", "U" + "1" * 32, "メイン", "yes"]
        )
        def warn_about_echo(_prompt):
            warnings.warn("echo fallback", getpass.GetPassWarning)
            return "must-not-be-collected"

        warning_prompts = GetPassManageLineChannelPrompts(
            input_stream=TTYStream(),
            input_fn=warning_harness.input,
            getpass_fn=warn_about_echo,
        )

        statuses = [
            empty_update.collect().status,
            fail_with(EOFError()).collect().status,
            fail_with(KeyboardInterrupt()).collect().status,
            warning_prompts.collect().status,
        ]

        self.assertEqual(statuses, ["invalid", "invalid", "invalid", "invalid"])

    # テストケース: hidden資格情報入力中に明示的な取消を指定する
    # 期待値: cancelledへ分類し、秘密を含むmutation用入力を生成しない
    def test_hidden_cancel_is_cancelled_without_mutation_input(self):
        harness = PromptHarness(
            ["register", "1234567890", "U" + "1" * 32, "メイン", "yes"],
            ["cancel"],
        )

        result = harness.prompts().collect()

        self.assertEqual(result.status, "cancelled")
