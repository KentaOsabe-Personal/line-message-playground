import hashlib
import io
import os
import secrets
import subprocess
import sys
from unittest.mock import patch
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import SimpleTestCase

from lineaccounts.runtime import (
    OwnerEligibilityUnavailable,
    derive_owner_digest,
    load_line_account_runtime,
    validate_django_secret,
)


def valid_environment() -> dict[str, str]:
    return {
        "LINE_LOGIN_CHANNEL_ID": "1234567890",
        "LINE_LOGIN_CHANNEL_SECRET": secrets.token_urlsafe(48),
        "LINE_LOGIN_PROVIDER_ID": "0012345678",
        "LINE_LIFF_LINKED_CHANNEL_PUBLIC_ID": "12345678-1234-5678-9234-567812345678",
        "LINE_OWNER_SUBJECT_DIGEST": "a" * 64,
    }


class LineAccountRuntimeTests(SimpleTestCase):
    # テストケース: canonical な LINE Login 環境値を runtime へ変換する。
    # 期待値: immutable な型付き値が得られ、raw secret と digest は repr に現れない。
    def test_loads_immutable_redacted_runtime(self):
        environment = valid_environment()
        runtime = load_line_account_runtime(environment)

        self.assertEqual(runtime.channel_id, "1234567890")
        self.assertEqual(runtime.provider_id, "0012345678")
        self.assertEqual(
            runtime.linked_channel_public_id,
            UUID("12345678-1234-5678-9234-567812345678"),
        )
        self.assertNotIn(environment["LINE_LOGIN_CHANNEL_SECRET"], repr(runtime))
        self.assertNotIn("a" * 64, repr(runtime))
        with self.assertRaises((AttributeError, TypeError)):
            runtime.provider_id = "999"  # type: ignore[misc]

    # テストケース: owner digest だけを未設定にして runtime を読み込む。
    # 期待値: 起動を許可する認証拒否 sentinel へ変換される。
    def test_missing_owner_digest_becomes_unavailable_sentinel(self):
        environment = valid_environment()
        environment.pop("LINE_OWNER_SUBJECT_DIGEST")

        runtime = load_line_account_runtime(environment)

        self.assertIsInstance(runtime.owner_eligibility, OwnerEligibilityUnavailable)

    # テストケース: 必須値の欠落・非canonical値を runtime へ読み込む。
    # 期待値: raw 値を含まない安定した起動エラーで fail closed になる。
    def test_rejects_missing_and_noncanonical_values_without_echo(self):
        invalid_values = {
            "LINE_LOGIN_CHANNEL_ID": " 123",
            "LINE_LOGIN_CHANNEL_SECRET": "",
            "LINE_LOGIN_PROVIDER_ID": "01 2",
            "LINE_LIFF_LINKED_CHANNEL_PUBLIC_ID": "not-a-uuid",
            "LINE_OWNER_SUBJECT_DIGEST": "digest-canary",
        }

        for key, invalid_value in invalid_values.items():
            environment = valid_environment()
            environment[key] = invalid_value
            with self.subTest(key=key), self.assertRaises(ImproperlyConfigured) as caught:
                load_line_account_runtime(environment)
            message = str(caught.exception)
            self.assertIn("LINE_ACCOUNT_RUNTIME_INVALID", message)
            if invalid_value:
                self.assertNotIn(invalid_value, message)
            configured_secret = environment["LINE_LOGIN_CHANNEL_SECRET"]
            if configured_secret:
                self.assertNotIn(configured_secret, message)

    # テストケース: 明示された安全な Django signing secret を検証する。
    # 期待値: 32文字以上かつ既知 default 以外の値だけが受理される。
    def test_accepts_safe_django_secret(self):
        secret = "safe-django-secret-" + "x" * 32

        self.assertEqual(validate_django_secret(secret), secret)

    # テストケース: 未設定・短い値・既知 default の Django secret を検証する。
    # 期待値: 値・長さ・断片を含まない起動エラーとして拒否される。
    def test_rejects_unsafe_django_secrets_without_echo(self):
        invalid_secrets = ("", "short-secret", "local-development-secret-key")

        for secret in invalid_secrets:
            with self.subTest(secret=secret), self.assertRaises(
                ImproperlyConfigured
            ) as caught:
                validate_django_secret(secret)
            message = str(caught.exception)
            self.assertEqual(message, "DJANGO_SECRET_KEY_INVALID")
            if secret:
                self.assertNotIn(secret, message)

    # テストケース: provider と LINE subject から owner digest を生成する。
    # 期待値: NUL区切り SHA-256 の canonical lowercase hex が得られる。
    def test_derives_canonical_owner_digest(self):
        expected = hashlib.sha256(b"0012345678\0owner-subject").hexdigest()

        self.assertEqual(
            derive_owner_digest("0012345678", "owner-subject"), expected
        )

    # テストケース: owner digest 未設定 runtime で非echo入力から commandを実行する。
    # 期待値: stdoutにはdigestだけが出力され、subjectはstdout/stderrへ現れない。
    @patch("lineaccounts.management.commands.derive_line_owner_digest.getpass")
    def test_digest_command_outputs_only_digest(self, getpass_mock):
        subject = f"U{secrets.token_hex(16)}"
        getpass_mock.return_value = subject
        stdout = io.StringIO()
        stderr = io.StringIO()

        call_command("derive_line_owner_digest", stdout=stdout, stderr=stderr)

        expected = hashlib.sha256(
            f"{valid_environment()['LINE_LOGIN_PROVIDER_ID']}\0{subject}".encode()
        ).hexdigest()
        self.assertEqual(stdout.getvalue(), f"{expected}\n")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(subject, stdout.getvalue() + stderr.getvalue())

    # テストケース: 既存のBackend専用LINE_USER_IDを入力源にdigest commandを実行する。
    # 期待値: subjectを表示せず、同じcanonical digestだけが出力される。
    def test_digest_command_can_use_backend_only_subject(self):
        subject = f"U{secrets.token_hex(16)}"
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.dict(os.environ, {"LINE_USER_ID": subject}):
            call_command(
                "derive_line_owner_digest",
                use_line_user_id=True,
                stdout=stdout,
                stderr=stderr,
            )

        expected = hashlib.sha256(
            f"{valid_environment()['LINE_LOGIN_PROVIDER_ID']}\0{subject}".encode()
        ).hexdigest()
        self.assertEqual(stdout.getvalue(), f"{expected}\n")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(subject, stdout.getvalue() + stderr.getvalue())

    # テストケース: test settings の起動用環境と Django settings を確認する。
    # 期待値: process固有secretがあり、LINE raw secret/digestはsettings属性へ載らない。
    def test_test_settings_bootstrap_keeps_line_secrets_out_of_settings(self):
        self.assertGreaterEqual(len(settings.SECRET_KEY), 32)
        self.assertNotEqual(settings.SECRET_KEY, "local-development-secret-key")
        self.assertTrue(os.environ["LINE_LOGIN_CHANNEL_SECRET"])
        self.assertFalse(hasattr(settings, "LINE_LOGIN_CHANNEL_SECRET"))
        self.assertFalse(hasattr(settings, "LINE_OWNER_SUBJECT_DIGEST"))

    # テストケース: 必須 LINE Login runtime 値を欠いた本番設定で Django を起動する。
    # 期待値: DB接続前にraw secretを含まない設定エラーで停止する。
    def test_production_startup_fails_before_database_access(self):
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
        environment["MYSQL_HOST"] = "database-must-not-be-contacted.invalid"
        environment.pop("LINE_LOGIN_CHANNEL_ID", None)

        completed = subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            cwd=settings.BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        combined = completed.stdout + completed.stderr

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("LINE_ACCOUNT_RUNTIME_INVALID", combined)
        self.assertNotIn(environment["LINE_LOGIN_CHANNEL_SECRET"], combined)
        self.assertNotIn("OperationalError", combined)

    # テストケース: raw LINE secretとowner digestを持つ本番設定を列挙する。
    # 期待値: diffsettingsへ環境値の属性名・値が公開されない。
    def test_diffsettings_does_not_publish_line_runtime_secrets(self):
        environment = os.environ.copy()
        secret = secrets.token_urlsafe(48)
        owner_digest = secrets.token_hex(32)
        environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
        environment["LINE_LOGIN_CHANNEL_SECRET"] = secret
        environment["LINE_OWNER_SUBJECT_DIGEST"] = owner_digest

        completed = subprocess.run(
            [sys.executable, "manage.py", "diffsettings", "--settings=config.settings"],
            cwd=settings.BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        combined = completed.stdout + completed.stderr

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("LINE_LOGIN_CHANNEL_SECRET", combined)
        self.assertNotIn("LINE_OWNER_SUBJECT_DIGEST", combined)
        self.assertNotIn(secret, combined)
        self.assertNotIn(owner_digest, combined)
