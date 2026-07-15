import os
import subprocess
import sys

from cryptography.fernet import Fernet
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings


class LineChannelsConfigTests(SimpleTestCase):
    # テストケース: Django app registryからチャネル基盤のAppConfigを取得する
    # 期待値: 明示的なLineChannelsConfigとして起動lifecycleへ登録されている
    def test_linechannels_app_is_registered_explicitly(self):
        app_config = apps.get_app_config("linechannels")

        self.assertEqual(type(app_config).__name__, "LineChannelsConfig")

    # テストケース: 専用鍵なし、不正鍵、SECRET_KEYだけ、DEBUG=Trueで本番設定を起動する
    # 期待値: DBへ接続せず、raw設定値を含まないImproperlyConfiguredで起動に失敗する
    def test_production_startup_fails_closed_for_invalid_configuration(self):
        valid_key = Fernet.generate_key().decode("ascii")
        cases = (
            ({}, None),
            ({"LINE_CHANNEL_CREDENTIAL_KEYS": ""}, None),
            ({"LINE_CHANNEL_CREDENTIAL_KEYS": "invalid-key-canary"}, "invalid-key-canary"),
            (
                {"LINE_CHANNEL_CREDENTIAL_KEYS": f"{valid_key},{valid_key}"},
                valid_key,
            ),
            ({"DJANGO_SECRET_KEY": "secret-key-only-canary"}, "secret-key-only-canary"),
            (
                {
                    "LINE_CHANNEL_CREDENTIAL_KEYS": valid_key,
                    "DJANGO_DEBUG": "true",
                },
                valid_key,
            ),
        )

        for additions, canary in cases:
            with self.subTest(additions=tuple(additions)):
                completed = self._run_django_setup(additions)
                combined = completed.stdout + completed.stderr
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("ImproperlyConfigured", combined)
                if canary:
                    self.assertNotIn(canary, combined)
                self.assertNotIn("OperationalError", combined)

    # テストケース: 正しい専用鍵とDEBUG=Falseで本番設定を起動する
    # 期待値: DB接続を必要とせずDjango setupが成功する
    def test_production_startup_accepts_valid_safe_configuration(self):
        completed = self._run_django_setup(
            {
                "LINE_CHANNEL_CREDENTIAL_KEYS": Fernet.generate_key().decode("ascii"),
                "DJANGO_DEBUG": "false",
            }
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    # テストケース: 同じ安全な設定でAppConfig.readyを複数回呼ぶ
    # 期待値: runtime stateを変更せず冪等に成功する
    @override_settings(DEBUG=False)
    def test_ready_is_idempotent(self):
        app_config = apps.get_app_config("linechannels")

        app_config.ready()
        app_config.ready()

    # テストケース: DEBUG=Trueへ上書きしてAppConfig.readyを呼ぶ
    # 期待値: raw設定や下位例外なしのImproperlyConfiguredへ変換する
    @override_settings(DEBUG=True)
    def test_ready_rejects_debug_mode(self):
        app_config = apps.get_app_config("linechannels")

        with self.assertRaisesRegex(ImproperlyConfigured, "startup configuration is invalid"):
            app_config.ready()

    # テストケース: Django settingsとDB logger設定を列挙する
    # 期待値: raw keyring属性はなく、DB loggerは出力・伝播しない
    def test_settings_do_not_publish_keyring_and_disable_database_logging(self):
        logger = settings.LOGGING["loggers"]["django.db.backends"]

        self.assertFalse(hasattr(settings, "LINE_CHANNEL_CREDENTIAL_KEYS"))
        self.assertEqual(logger["handlers"], ["null"])
        self.assertFalse(logger["propagate"])

    # テストケース: canary keyで本番設定のdiffsettingsを実行する
    # 期待値: stdout/stderrへraw keyringの属性名・値を出さない
    def test_diffsettings_does_not_expose_raw_keyring(self):
        raw_key = Fernet.generate_key().decode("ascii")
        environment = self._base_environment()
        environment.update(
            {
                "LINE_CHANNEL_CREDENTIAL_KEYS": raw_key,
                "DJANGO_DEBUG": "false",
            }
        )
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
        self.assertNotIn("LINE_CHANNEL_CREDENTIAL_KEYS", combined)
        self.assertNotIn(raw_key, combined)

    @classmethod
    def _run_django_setup(cls, additions):
        environment = cls._base_environment()
        environment.update(additions)
        return subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            cwd=settings.BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _base_environment():
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
        environment["DJANGO_DEBUG"] = "false"
        environment["MYSQL_HOST"] = "database-must-not-be-contacted.invalid"
        environment.pop("LINE_CHANNEL_CREDENTIAL_KEYS", None)
        environment.pop("DJANGO_SECRET_KEY", None)
        return environment
