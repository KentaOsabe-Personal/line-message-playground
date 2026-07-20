from unittest.mock import MagicMock, patch

import MySQLdb

from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from lineaccounts.unlink_execution_lock import MySQLUnlinkExecutionLock


class MySQLUnlinkExecutionLockTests(SimpleTestCase):
    def connection_with_results(self, acquire=1, release=1):
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.side_effect = ((acquire,), (release,))
        return connection, cursor

    # テストケース: owner slot由来のadvisory lockを取得して正常終了する
    # 期待値: wait 0で取得し同じconnectionから必ず解放する
    def test_acquires_and_releases_on_same_connection(self):
        connection, cursor = self.connection_with_results()

        with patch(
            "lineaccounts.unlink_execution_lock.connections",
            {"default": connection},
        ):
            with MySQLUnlinkExecutionLock().acquire(1) as acquired:
                self.assertTrue(acquired)

        self.assertEqual(cursor.execute.call_count, 2)
        self.assertEqual(cursor.execute.call_args_list[0].args[1], ["lineaccounts-unlink-owner-1-v1"])

    # テストケース: 別requestが同じowner lockを保持中に取得する
    # 期待値: busyをFalseで返し既存所有権を解放しない
    def test_busy_returns_false_without_releasing_foreign_lock(self):
        connection, cursor = self.connection_with_results(acquire=0, release=0)

        with patch(
            "lineaccounts.unlink_execution_lock.connections",
            {"default": connection},
        ):
            with MySQLUnlinkExecutionLock().acquire(1) as acquired:
                self.assertFalse(acquired)

        self.assertEqual(cursor.execute.call_count, 1)


class MySQLUnlinkExecutionLockIntegrationTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "mysql":
            self.skipTest("MySQL advisory lock contract")
        connection.ensure_connection()
        self.probe = MySQLdb.connect(**connection.get_connection_params())

    def tearDown(self):
        if hasattr(self, "probe"):
            self.probe.close()

    def probe_lock(self, function):
        cursor = self.probe.cursor()
        try:
            cursor.execute(
                f"SELECT {function}(%s{', 0' if function == 'GET_LOCK' else ''})",
                ("lineaccounts-unlink-owner-1-v1",),
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    # テストケース: 別connectionがowner unlink lockを保持中に取得する
    # 期待値: waitなしでbusyとなりLINE実行側へ進まず、解放後は再取得できる
    def test_busy_owner_lock_can_be_reacquired_after_owner_releases(self):
        self.assertEqual(self.probe_lock("GET_LOCK"), 1)
        try:
            with MySQLUnlinkExecutionLock().acquire(1) as acquired:
                self.assertFalse(acquired)
        finally:
            self.assertEqual(self.probe_lock("RELEASE_LOCK"), 1)

        with MySQLUnlinkExecutionLock().acquire(1) as acquired:
            self.assertTrue(acquired)

    # テストケース: 別connectionがunlink lockを保持中に競合requestがdeauthorizeへ進もうとする。
    # 期待値: 競合requestはlock取得に失敗し、LINE呼出しcallbackを一度も実行しない。
    def test_competing_unlink_never_reaches_line_while_mysql_lock_is_held(self):
        line_calls = 0
        self.assertEqual(self.probe_lock("GET_LOCK"), 1)
        try:
            with MySQLUnlinkExecutionLock().acquire(1) as acquired:
                if acquired:
                    line_calls += 1
        finally:
            self.assertEqual(self.probe_lock("RELEASE_LOCK"), 1)

        self.assertEqual(line_calls, 0)

    # テストケース: lock所有connectionが明示releaseなしで喪失する
    # 期待値: MySQLが自動解放し別connectionからwaitなしで再取得できる
    def test_connection_loss_automatically_releases_owned_lock(self):
        self.assertEqual(self.probe_lock("GET_LOCK"), 1)
        self.probe.close()
        self.probe = MySQLdb.connect(**connection.get_connection_params())

        with MySQLUnlinkExecutionLock().acquire(1) as acquired:
            self.assertTrue(acquired)
