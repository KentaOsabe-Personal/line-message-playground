import base64
import os
import pickle
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import SimpleTestCase

from linechannels.runtime import (
    CredentialKeyringConfigurationError,
    _reset_runtime_state_for_tests,
    get_validated_keyring,
    load_credential_keyring,
)


class RuntimeKeyringTests(SimpleTestCase):
    def setUp(self):
        _reset_runtime_state_for_tests()

    def tearDown(self):
        _reset_runtime_state_for_tests()

    # テストケース: canonical Fernet keyring を同じ設定で複数回ロードする
    # 期待値: 初回だけprivate stateを作り、同一objectを冪等に取得できる
    def test_loads_valid_keyring_idempotently(self):
        raw = ",".join((self._new_key(), self._new_key()))

        with patch.dict(os.environ, {"LINE_CHANNEL_CREDENTIAL_KEYS": raw}, clear=False):
            load_credential_keyring()
            first = get_validated_keyring()
            load_credential_keyring()

        self.assertIs(get_validated_keyring(), first)

    # テストケース: 未指定、空、空要素、空白、quote、改行、非ASCII、不正keyをロードする
    # 期待値: raw値を含まない安全な設定エラーとなり、stateを作らない
    def test_rejects_invalid_keyring_grammar_without_exposing_input(self):
        key = self._new_key()
        invalid_values = (
            None,
            "",
            f"{key},",
            f"{key}, {self._new_key()}",
            f'"{key}"',
            f"{key}\n",
            "鍵",
            "not-a-fernet-key",
            base64.b64encode(b"\xff" * 32).decode("ascii"),
        )

        for raw in invalid_values:
            with self.subTest(raw_is_missing=raw is None):
                _reset_runtime_state_for_tests()
                environment = {} if raw is None else {"LINE_CHANNEL_CREDENTIAL_KEYS": raw}
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(CredentialKeyringConfigurationError) as raised:
                        load_credential_keyring()
                self.assertEqual(str(raised.exception), "credential_keyring_invalid")
                if raw:
                    self.assertNotIn(raw, repr(raised.exception))
                with self.assertRaises(CredentialKeyringConfigurationError):
                    get_validated_keyring()

    # テストケース: decode後に同じ鍵となる重複keyringをロードする
    # 期待値: 補正や重複除去をせず、安全な設定エラーとして拒否する
    def test_rejects_duplicate_keys(self):
        key = self._new_key()

        with patch.dict(os.environ, {"LINE_CHANNEL_CREDENTIAL_KEYS": f"{key},{key}"}, clear=False):
            with self.assertRaisesRegex(
                CredentialKeyringConfigurationError,
                "credential_keyring_invalid",
            ):
                load_credential_keyring()

    # テストケース: 初期化後に異なるraw keyringで再初期化する
    # 期待値: 既存stateを維持し、秘密なしの再初期化エラーを返す
    def test_rejects_reinitialization_with_different_keyring(self):
        first_raw = self._new_key()
        second_raw = self._new_key()
        with patch.dict(os.environ, {"LINE_CHANNEL_CREDENTIAL_KEYS": first_raw}, clear=False):
            load_credential_keyring()
            first = get_validated_keyring()
        with patch.dict(os.environ, {"LINE_CHANNEL_CREDENTIAL_KEYS": second_raw}, clear=False):
            with self.assertRaisesRegex(
                CredentialKeyringConfigurationError,
                "credential_keyring_already_initialized",
            ):
                load_credential_keyring()

        self.assertIs(get_validated_keyring(), first)

    # テストケース: 検証済みkeyringを表示、列挙、長さ取得、pickle化する
    # 期待値: 鍵値・鍵数を公開せず、redacted表示と直列化拒否だけを提供する
    def test_validated_keyring_is_opaque_and_not_serializable(self):
        raw = ",".join((self._new_key(), self._new_key()))
        with patch.dict(os.environ, {"LINE_CHANNEL_CREDENTIAL_KEYS": raw}, clear=False):
            load_credential_keyring()
        keyring = get_validated_keyring()

        self.assertEqual(str(keyring), "<ValidatedCredentialKeyring redacted>")
        self.assertEqual(repr(keyring), "<ValidatedCredentialKeyring redacted>")
        self.assertNotIn(raw, repr(keyring))
        with self.assertRaises(TypeError):
            iter(keyring)
        with self.assertRaises(TypeError):
            len(keyring)
        with self.assertRaises(TypeError):
            vars(keyring)
        with self.assertRaisesRegex(TypeError, "serialization is disabled"):
            pickle.dumps(keyring)

    # テストケース: 未初期化stateを取得後、環境へ鍵を遅延設定する
    # 期待値: getは環境を再読込せず、明示loadまで安全に失敗する
    def test_get_does_not_lazily_read_environment(self):
        with patch.dict(os.environ, {"LINE_CHANNEL_CREDENTIAL_KEYS": self._new_key()}, clear=False):
            with self.assertRaisesRegex(
                CredentialKeyringConfigurationError,
                "credential_keyring_not_initialized",
            ):
                get_validated_keyring()

    @staticmethod
    def _new_key() -> str:
        return Fernet.generate_key().decode("ascii")
