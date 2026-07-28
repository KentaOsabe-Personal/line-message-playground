from django.test import SimpleTestCase

from delivery.formatters import (
    MessageValidationError,
    count_utf16_code_units,
    format_message_snapshot,
    format_message,
)
from delivery.types import MessageSnapshot


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

    # テストケース: BMP文字、結合文字、絵文字を含む同じ入力を複数回整形する。
    # 期待値: Unicodeを正規化で変更せず、UTF-16単位数とfingerprintが安定する。
    def test_preserves_unicode_sequences_and_stable_fingerprint(self):
        subject = "漢字e\u0301😀"
        body = "本文\ne\u0301と😀"

        first = format_message(subject, body)
        second = format_message(subject, body)

        self.assertEqual(first.formatted_text, "【漢字e\u0301😀】\n\n本文\ne\u0301と😀")
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            count_utf16_code_units(first.formatted_text),
            len(first.formatted_text.encode("utf-16-le")) // 2,
        )


class LinkedRecipientMessageFormatterTests(SimpleTestCase):
    # テストケース: 新しい配信経路で件名と改行を含む本文を整形する。
    # 期待値: 既存formatterと同じ整形済みtextとfingerprintのsnapshotへ収束する。
    def test_builds_same_canonical_snapshot_as_existing_formatter(self):
        legacy = format_message("件名", "1行目\n2行目")

        snapshot = format_message_snapshot("件名", "1行目\n2行目")

        self.assertIsInstance(snapshot, MessageSnapshot)
        self.assertEqual(snapshot.subject, legacy.subject)
        self.assertEqual(snapshot.body, legacy.body)
        self.assertEqual(snapshot.formatted_text, "【件名】\n\n1行目\n2行目")
        self.assertEqual(snapshot.fingerprint, legacy.fingerprint)

    # テストケース: 新しい配信経路の件名または本文へ空白だけを指定する。
    # 期待値: 該当fieldを示す検証エラーとなり、snapshotは生成されない。
    def test_rejects_blank_fields_before_snapshot_creation(self):
        for subject, body, field in (
            (" \t", "本文", "subject"),
            ("件名", "\n ", "body"),
        ):
            with (
                self.subTest(field=field),
                self.assertRaises(MessageValidationError) as raised,
            ):
                format_message_snapshot(subject, body)

            self.assertEqual(raised.exception.code, "blank")
            self.assertEqual(raised.exception.field, field)

    # テストケース: receipt表示文言とは独立にUTF-16で5,000単位と5,001単位の本文を整形する。
    # 期待値: text一件だけの長さで境界判定し、receipt template文言を混入しない。
    def test_enforces_text_limit_without_receipt_template_copy(self):
        receipt_template_copy = "受け取りました"
        prefix_units = count_utf16_code_units("【s】\n\n")
        body = "a" * (5000 - prefix_units)

        snapshot = format_message_snapshot("s", body)

        self.assertEqual(count_utf16_code_units(snapshot.formatted_text), 5000)
        self.assertNotIn(receipt_template_copy, snapshot.formatted_text)
        with self.assertRaises(MessageValidationError) as raised:
            format_message_snapshot("s", body + "a")
        self.assertEqual(raised.exception.code, "message_too_long")
