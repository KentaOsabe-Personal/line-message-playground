import io
import uuid
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from linechannels.rotation import RotationItemFailure, RotationSummary


COMMAND_PATH = "linechannels.management.commands.rotate_line_channel_credentials"


class RotateLineChannelCredentialsCommandTests(SimpleTestCase):
    def invoke(self, summary=None, *, side_effect=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        service = Mock()
        service.rotate_all.return_value = summary
        service.rotate_all.side_effect = side_effect
        with patch(f"{COMMAND_PATH}.build_rotation_service", return_value=service) as factory:
            try:
                call_command(
                    "rotate_line_channel_credentials",
                    stdout=stdout,
                    stderr=stderr,
                )
            except CommandError as error:
                captured = error
            else:
                captured = None
        factory.assert_called_once_with()
        service.rotate_all.assert_called_once_with()
        return stdout.getvalue(), stderr.getvalue(), captured

    # テストケース: 全資格情報のprimary-only最終検証が成功する
    # 期待値: 件数と旧鍵撤去可否だけをstdoutへ出し、正常終了する
    def test_complete_outputs_safe_counts_and_succeeds(self):
        summary = RotationSummary("complete", 2, 3, 0, (), True)

        stdout, stderr, error = self.invoke(summary)

        self.assertIsNone(error)
        self.assertIn('"status": "complete"', stdout)
        self.assertIn('"verified_count": 2', stdout)
        self.assertIn('"rotated_count": 3', stdout)
        self.assertIn('"old_keys_removable": true', stdout)
        self.assertEqual(stderr, "")

    # テストケース: 行失敗が残るincomplete結果を受け取る
    # 期待値: 公開UUIDとsafe codeだけをstderrへ出し、非完了exitへ写像する
    def test_incomplete_outputs_public_failures_and_fails_exit(self):
        public_id = uuid.uuid4()
        summary = RotationSummary(
            "incomplete",
            1,
            1,
            1,
            (RotationItemFailure(public_id, "credential_unreadable"),),
            False,
        )

        stdout, stderr, error = self.invoke(summary)

        self.assertIsInstance(error, CommandError)
        self.assertEqual(stdout, "")
        self.assertIn(str(public_id), stderr)
        self.assertIn("credential_unreadable", stderr)
        self.assertNotIn("ciphertext", stderr)
        self.assertEqual(str(error), "credential rotation incomplete")

    # テストケース: batch lock競合または旧鍵未設定の結果を受け取る
    # 期待値: 行変更のない行動可能なstatusだけを出し、どちらも非完了exitへ写像する
    def test_busy_and_configuration_required_are_non_success_exits(self):
        for status in ("busy", "configuration_required"):
            with self.subTest(status=status):
                summary = RotationSummary(status, 0, 0, 0, (), False)

                stdout, stderr, error = self.invoke(summary)

                self.assertEqual(stdout, "")
                self.assertIn(f'"status": "{status}"', stderr)
                self.assertIsInstance(error, CommandError)
                self.assertEqual(str(error), "credential rotation incomplete")

    # テストケース: serviceが秘密を含む可能性のある予期しない例外または割込みを送出する
    # 期待値: raw内容を固定CommandErrorへ置換し、stdout/stderrにも連結しない
    def test_unexpected_error_and_interrupt_never_expose_lower_details(self):
        canary = "key-plaintext-ciphertext-canary"
        for error in (RuntimeError(canary), KeyboardInterrupt(canary)):
            with self.subTest(error_type=type(error).__name__):
                stdout, stderr, captured = self.invoke(side_effect=error)
                combined = stdout + stderr + str(captured)
                self.assertIsInstance(captured, CommandError)
                self.assertNotIn(canary, combined)
                self.assertEqual(str(captured), "credential rotation failed")

    # テストケース: rotation commandの公開parserを調べる
    # 期待値: 鍵・token・secret・ciphertextを受け取るoptionやargumentを一つも持たない
    def test_command_has_no_credential_or_key_inputs(self):
        from linechannels.management.commands.rotate_line_channel_credentials import (
            Command,
        )

        parser = Command().create_parser("manage.py", "rotate_line_channel_credentials")
        destinations = {action.dest.lower() for action in parser._actions}

        for forbidden in ("key", "token", "secret", "credential", "ciphertext"):
            self.assertTrue(all(forbidden not in dest for dest in destinations))
