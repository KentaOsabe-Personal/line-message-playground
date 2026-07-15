import io
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from linechannels.types import (
    ChannelMutationFailed,
    ChannelMutationSucceeded,
    ManageLineChannelInputCancelled,
    ManageLineChannelInputCollected,
    ManageLineChannelInputInvalid,
    PublicChannelSummary,
    RegisterLineChannel,
    SetLineChannelActive,
    UpdateLineChannel,
)
from linechannels.validators import build_credential_pair


COMMAND_PATH = "linechannels.management.commands.manage_line_channel"


class ManageLineChannelCommandTests(SimpleTestCase):
    def setUp(self):
        self.public_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        self.success = ChannelMutationSucceeded(
            PublicChannelSummary(
                public_id=self.public_id,
                messaging_api_channel_id="1234567890",
                bot_user_id="U" + "1" * 32,
                label="メイン",
                is_active=True,
                credentials_configured=True,
                created_at=now,
                updated_at=now,
            )
        )

    def invoke(self, prompt_result, service):
        stdout = io.StringIO()
        stderr = io.StringIO()
        prompts = Mock()
        prompts.collect.return_value = prompt_result
        with patch(
            f"{COMMAND_PATH}.build_manage_line_channel_prompts",
            return_value=prompts,
        ) as prompt_factory:
            with patch(
                f"{COMMAND_PATH}.build_line_channel_service",
                return_value=service,
            ) as service_factory:
                call_command("manage_line_channel", stdout=stdout, stderr=stderr)
        prompt_factory.assert_called_once_with()
        service_factory.assert_called_once_with()
        prompts.collect.assert_called_once_with()
        return stdout.getvalue(), stderr.getvalue()

    # テストケース: 登録、更新、有効化、無効化の各型付き入力をcommandへ渡す
    # 期待値: 対応するservice操作だけを1回呼び、公開情報だけを出力する
    def test_dispatches_each_typed_input_to_exactly_one_service_operation(self):
        register = RegisterLineChannel(
            "1234567890",
            "U" + "1" * 32,
            "メイン",
            build_credential_pair("token-canary", "secret-canary"),
            True,
        )
        inputs = (
            (register, "register", (register,)),
            (UpdateLineChannel(self.public_id, label="更新"), "update", (UpdateLineChannel(self.public_id, label="更新"),)),
            (SetLineChannelActive(self.public_id, True), "set_active", (self.public_id, True)),
            (SetLineChannelActive(self.public_id, False), "set_active", (self.public_id, False)),
        )

        for value, expected_method, expected_args in inputs:
            with self.subTest(expected_method=expected_method, value=value):
                service = Mock()
                getattr(service, expected_method).return_value = self.success

                stdout, stderr = self.invoke(
                    ManageLineChannelInputCollected(value), service
                )

                getattr(service, expected_method).assert_called_once_with(*expected_args)
                self.assertEqual(
                    service.register.call_count
                    + service.update.call_count
                    + service.set_active.call_count,
                    1,
                )
                self.assertIn(str(self.public_id), stdout)
                self.assertEqual(stderr, "")
                self.assertNotIn("token-canary", stdout + stderr)
                self.assertNotIn("secret-canary", stdout + stderr)

    # テストケース: prompt境界がcancelledまたはinvalidを返す
    # 期待値: service mutationを呼ばず、安全な結果分類だけを出力する
    def test_cancelled_and_invalid_input_never_call_service_mutation(self):
        for prompt_result, expected_stream in (
            (ManageLineChannelInputCancelled(), "cancelled"),
            (ManageLineChannelInputInvalid(), "invalid"),
        ):
            service = Mock()

            stdout, stderr = self.invoke(prompt_result, service)

            service.register.assert_not_called()
            service.update.assert_not_called()
            service.set_active.assert_not_called()
            self.assertIn(expected_stream, stdout + stderr)

    # テストケース: serviceが安全な失敗または秘密を含み得る予期しない例外を返す
    # 期待値: safe codeだけを出力し、下位例外の内容を固定CommandErrorへ置換する
    def test_safe_failure_and_unexpected_error_never_expose_lower_details(self):
        command = UpdateLineChannel(self.public_id, label="更新")
        failed_service = Mock()
        failed_service.update.return_value = ChannelMutationFailed("channel_not_found")

        stdout, stderr = self.invoke(
            ManageLineChannelInputCollected(command), failed_service
        )

        self.assertIn("channel_not_found", stdout + stderr)
        canary = "raw-secret-error-canary"
        broken_service = Mock()
        broken_service.update.side_effect = RuntimeError(canary)
        with self.assertRaises(CommandError) as captured:
            self.invoke(ManageLineChannelInputCollected(command), broken_service)
        self.assertNotIn(canary, str(captured.exception))
