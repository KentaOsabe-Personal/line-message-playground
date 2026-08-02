from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from linechannels.models import LineChannel


class RichMenuInitialMigrationTests(TransactionTestCase):
    migrate_from = [("linerichmenus", None)]
    migrate_to = [("linerichmenus", "0001_initial")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    # テストケース: 既存チャネル行があるDBへrich menu初期migrationを適用する。
    # 期待値: 既存行を変更せず、独立した4テーブルだけを追加する。
    def test_initial_migration_adds_only_four_tables_and_preserves_existing_rows(self):
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id="U" + uuid4().hex,
            label="既存チャネル",
            provider_id="0012345678",
            is_active=True,
        )
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        before_tables = set(connection.introspection.table_names())

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        after_tables = set(connection.introspection.table_names())

        self.assertEqual(
            after_tables - before_tables,
            {
                "linerichmenus_channelstate",
                "linerichmenus_operation",
                "linerichmenus_resource",
                "linerichmenus_transition",
            },
        )
        preserved = LineChannel.objects.get(pk=channel.pk)
        self.assertEqual(preserved.label, "既存チャネル")
        self.assertEqual(preserved.provider_id, "0012345678")

    # テストケース: rich menu初期migrationのoperation種類を検査する。
    # 期待値: 既存appへのdata/field変更を含まず、4モデル作成と循環relation追加だけである。
    def test_initial_migration_contains_no_data_or_existing_app_mutations(self):
        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration("linerichmenus", "0001_initial")

        self.assertEqual(
            {operation.__class__.__name__ for operation in migration.operations},
            {"CreateModel", "AddField", "AddIndex", "AddConstraint"},
        )
        self.assertEqual(
            sum(
                operation.__class__.__name__ == "CreateModel"
                for operation in migration.operations
            ),
            4,
        )
