from unittest.mock import MagicMock, patch

import MySQLdb

from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TransactionTestCase

from linechannels.rotation_lock import MySQLRotationLock, RotationLock, RotationLockError


class MySQLRotationLockTests(SimpleTestCase):
    def connection_with_results(self, acquire_result=1, release_result=1):
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.side_effect = ((acquire_result,), (release_result,))
        return connection, cursor

    # テストケース: advisory lockを取得してcontextを正常終了する
    # 期待値: 同じconnectionで取得成功を返し、終了時に明示解放を1回実行する
    def test_acquired_lock_is_released_on_normal_exit(self):
        connection, cursor = self.connection_with_results()

        with patch("linechannels.rotation_lock.connections", {"default": connection}):
            with MySQLRotationLock().acquire() as acquired:
                self.assertTrue(acquired)

        self.assertEqual(cursor.execute.call_count, 2)
        self.assertEqual(cursor.fetchone.call_count, 2)

    # テストケース: 別processがlockを保持しているbusy状態で取得を試みる
    # 期待値: 例外でなくFalseを返し、終了処理で明示解放を試みる
    def test_busy_is_safe_false_and_still_runs_release(self):
        connection, cursor = self.connection_with_results(
            acquire_result=0, release_result=0
        )

        with patch("linechannels.rotation_lock.connections", {"default": connection}):
            with MySQLRotationLock().acquire() as acquired:
                self.assertFalse(acquired)

        self.assertEqual(cursor.execute.call_count, 2)

    # テストケース: lock保持中に予期しない例外または割込みが発生する
    # 期待値: 元の例外を維持しつつ、各経路で明示解放を1回実行する
    def test_releases_lock_for_unexpected_error_and_interrupt(self):
        for error in (RuntimeError("canary"), KeyboardInterrupt()):
            with self.subTest(error_type=type(error).__name__):
                connection, cursor = self.connection_with_results()
                with patch(
                    "linechannels.rotation_lock.connections", {"default": connection}
                ):
                    with self.assertRaises(type(error)):
                        with MySQLRotationLock().acquire() as acquired:
                            self.assertTrue(acquired)
                            raise error
                self.assertEqual(cursor.execute.call_count, 2)

    # テストケース: advisory lockの取得または解放でDB障害が発生する
    # 期待値: SQL・lock名・接続情報を含まないstorage_unavailableへ置換する
    def test_database_failures_are_redacted_and_safely_classified(self):
        canary = "sql-lock-connection-canary"
        connection = MagicMock()
        connection.cursor.return_value.__enter__.side_effect = DatabaseError(canary)

        with patch("linechannels.rotation_lock.connections", {"default": connection}):
            with self.assertRaises(RotationLockError) as captured:
                with MySQLRotationLock().acquire():
                    self.fail("取得失敗時はbodyへ到達しない")
        self.assertEqual(captured.exception.code, "storage_unavailable")
        self.assertNotIn(canary, str(captured.exception))
        self.assertNotIn(canary, repr(captured.exception))

        connection, cursor = self.connection_with_results()
        cursor.execute.side_effect = (None, DatabaseError(canary))
        with patch("linechannels.rotation_lock.connections", {"default": connection}):
            with self.assertRaises(RotationLockError) as captured:
                with MySQLRotationLock().acquire() as acquired:
                    self.assertTrue(acquired)
        self.assertEqual(captured.exception.code, "storage_unavailable")
        self.assertNotIn(canary, str(captured.exception))

    # テストケース: advisory lock SQLが欠損・NULL・不正な戻り値を返す
    # 期待値: busyや正常解放へ誤分類せず、storage_unavailableとして拒否する
    def test_invalid_acquire_and_release_results_are_storage_failures(self):
        invalid_acquire_rows = (None, (None,), (2,))
        for invalid_row in invalid_acquire_rows:
            with self.subTest(invalid_acquire_row=invalid_row):
                connection, cursor = self.connection_with_results()
                cursor.fetchone.side_effect = (invalid_row, (None,))
                with patch(
                    "linechannels.rotation_lock.connections", {"default": connection}
                ):
                    with self.assertRaises(RotationLockError) as captured:
                        with MySQLRotationLock().acquire():
                            self.fail("不正取得結果ではbodyへ到達しない")
                self.assertEqual(captured.exception.code, "storage_unavailable")

        for invalid_row in ((0,), (None,), None):
            with self.subTest(invalid_release_row=invalid_row):
                connection, cursor = self.connection_with_results()
                cursor.fetchone.side_effect = ((1,), invalid_row)
                with patch(
                    "linechannels.rotation_lock.connections", {"default": connection}
                ):
                    with self.assertRaises(RotationLockError) as captured:
                        with MySQLRotationLock().acquire() as acquired:
                            self.assertTrue(acquired)
                self.assertEqual(captured.exception.code, "storage_unavailable")

    # テストケース: Django具象lockをrotation専用公開contractとして扱う
    # 期待値: RotationLock Protocolへ構造的に適合する
    def test_concrete_lock_implements_public_protocol(self):
        self.assertIsInstance(MySQLRotationLock(), RotationLock)


class MySQLRotationLockIntegrationTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "mysql":
            self.skipTest("MySQL advisory lock contract")
        connection.ensure_connection()
        self.probe = MySQLdb.connect(**connection.get_connection_params())

    def tearDown(self):
        if hasattr(self, "probe"):
            self.probe.close()

    def probe_acquire(self):
        cursor = self.probe.cursor()
        try:
            cursor.execute(
                "SELECT GET_LOCK(%s, 0)",
                ("linechannels-credential-rotation-v1",),
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def probe_release(self):
        cursor = self.probe.cursor()
        try:
            cursor.execute(
                "SELECT RELEASE_LOCK(%s)",
                ("linechannels-credential-rotation-v1",),
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    # テストケース: 正常・予期しない例外・割込みの各context終了後に別connectionで取得する
    # 期待値: どの終了経路でも同じlockを別connectionが直ちに再取得できる
    def test_another_connection_can_reacquire_after_every_owned_exit(self):
        for error in (None, RuntimeError("canary"), KeyboardInterrupt()):
            with self.subTest(error_type=type(error).__name__ if error else "normal"):
                try:
                    if error is None:
                        with MySQLRotationLock().acquire() as acquired:
                            self.assertTrue(acquired)
                    else:
                        with self.assertRaises(type(error)):
                            with MySQLRotationLock().acquire() as acquired:
                                self.assertTrue(acquired)
                                raise error
                    self.assertEqual(self.probe_acquire(), 1)
                finally:
                    self.probe_release()

    # テストケース: 別connectionがlock保持中にrotation lock取得を試みる
    # 期待値: busy=Falseとなり、既存lockの所有権を奪わずDB行処理へ進まない
    def test_busy_does_not_take_lock_from_another_connection(self):
        self.assertEqual(self.probe_acquire(), 1)
        try:
            with MySQLRotationLock().acquire() as acquired:
                self.assertFalse(acquired)
            self.assertEqual(self.probe_acquire(), 1)
        finally:
            self.probe_release()
