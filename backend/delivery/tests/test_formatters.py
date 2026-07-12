from django.test import SimpleTestCase, override_settings

from delivery.confirmation import ConfirmationError, ConfirmationTokenService
from delivery.formatters import (
    FormattedMessage,
    MessageValidationError,
    count_utf16_code_units,
    format_message,
)


class MessageFormatterTests(SimpleTestCase):
    # テストケース: 件名と改行を含む本文を整形する。
    # 期待値: 指定形式と改行が保持され、同じ入力のfingerprintが安定する。
    def test_formats_message_and_stable_fingerprint(self):
        first = format_message("件名", "1行目\n2行目")
        second = format_message("件名", "1行目\n2行目")

        self.assertEqual(first.formatted_text, "【件名】\n\n1行目\n2行目")
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(len(first.fingerprint), 64)

    # テストケース: 件名と本文へ空白だけを指定する。
    # 期待値: 各項目を識別できる検証エラーとして拒否される。
    def test_rejects_blank_fields_separately(self):
        for subject, body, field in (("  ", "body", "subject"), ("subject", "\n\t", "body")):
            with self.subTest(field=field), self.assertRaises(MessageValidationError) as raised:
                format_message(subject, body)
            self.assertEqual(raised.exception.field, field)

    # テストケース: UTF-16で5,000単位と5,001単位の整形済みテキストを生成する。
    # 期待値: 5,000は受理され、5,001は長さ超過として拒否される。
    def test_enforces_utf16_boundary(self):
        prefix_units = count_utf16_code_units("【s】\n\n")
        accepted = format_message("s", "a" * (5000 - prefix_units))
        self.assertEqual(count_utf16_code_units(accepted.formatted_text), 5000)

        with self.assertRaises(MessageValidationError) as raised:
            format_message("s", "a" * (5001 - prefix_units))
        self.assertEqual(raised.exception.code, "message_too_long")

    # テストケース: 絵文字と孤立surrogateのUTF-16長を検証する。
    # 期待値: 絵文字は2単位で数え、孤立surrogateは検証エラーになる。
    def test_counts_emoji_and_rejects_lone_surrogate(self):
        self.assertEqual(count_utf16_code_units("😀"), 2)
        with self.assertRaises(MessageValidationError):
            count_utf16_code_units("\ud800")


class ConfirmationTokenServiceTests(SimpleTestCase):
    # テストケース: preview済み内容のtokenを同じ内容と変更内容で検証する。
    # 期待値: 同じ内容だけ成功し、変更内容と改変tokenは確認エラーになる。
    def test_only_confirmed_content_is_accepted(self):
        service = ConfirmationTokenService()
        message = format_message("件名", "本文")
        token = service.issue(message)

        service.verify(token, message)
        with self.assertRaises(ConfirmationError):
            service.verify(token, format_message("件名", "変更"))
        with self.assertRaises(ConfirmationError):
            service.verify(token + "x", message)

        old_version = FormattedMessage(
            subject=message.subject,
            body=message.body,
            formatted_text=message.formatted_text,
            fingerprint=message.fingerprint,
            formatter_version=message.formatter_version + 1,
        )
        with self.assertRaises(ConfirmationError):
            service.verify(token, old_version)

    # テストケース: 発行したopaque tokenの復号payloadを確認する。
    # 期待値: versionとfingerprintだけを含み、入力本文や操作IDを含まない。
    def test_token_payload_contains_only_version_and_fingerprint(self):
        service = ConfirmationTokenService()
        message = format_message("secret-subject", "secret-body")
        token = service.issue(message)

        payload = service.decode_for_test(token)
        self.assertEqual(set(payload), {"v", "fp"})
        self.assertNotIn("secret-subject", token)
        self.assertNotIn("secret-body", token)
