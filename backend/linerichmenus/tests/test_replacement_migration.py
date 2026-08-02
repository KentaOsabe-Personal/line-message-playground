from datetime import UTC, datetime
from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class RichMenuReplacementMigrationTests(TransactionTestCase):
    migrate_from = [("linerichmenus", "0001_initial")]
    migrate_to = [("linerichmenus", "0002_resource_replacement_operation")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    # テストケース: 既存resource行がある0001 schemaへreplacement relationを追加する。
    # 期待値: 既存行を更新せずnullable relationだけが追加される。
    def test_migration_adds_nullable_relation_without_backfill(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        apps = executor.loader.project_state(self.migrate_from).apps
        State = apps.get_model("linerichmenus", "RichMenuChannelState")
        Operation = apps.get_model("linerichmenus", "RichMenuOperation")
        Resource = apps.get_model("linerichmenus", "ManagedRichMenu")
        state = State.objects.create(channel_public_id=uuid4())
        operation = Operation.objects.create(
            operation_id=uuid4(), channel_state=state,
            owner_identity_public_id=uuid4(), provider_id="0012345678",
            kind="apply", request_fingerprint="a" * 64,
            expected_channel_revision=NOW, status="accepted", stage=None,
            result_code="accepted", accepted_at=NOW,
        )
        resource = Resource.objects.create(
            channel_state=state, origin_operation=operation,
            ownership_marker="existing-" + uuid4().hex,
            lifecycle="candidate", image_digest="b" * 64,
        )
        legacy_old = Resource.objects.create(
            channel_state=state, origin_operation=operation,
            ownership_marker="legacy-old-" + uuid4().hex,
            lifecycle="old", image_digest="c" * 64,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        MigratedResource = migrated_apps.get_model("linerichmenus", "ManagedRichMenu")
        migrated = MigratedResource.objects.get(pk=resource.pk)
        self.assertIsNone(migrated.replacement_operation_id)
        self.assertEqual(migrated.ownership_marker, resource.ownership_marker)
        migrated_old = MigratedResource.objects.get(pk=legacy_old.pk)
        self.assertEqual(migrated_old.lifecycle, "old")
        self.assertIsNone(migrated_old.replacement_operation_id)

    # テストケース: replacement migrationのoperation種類を検査する。
    # 期待値: field/constraint追加だけでRunPythonや既存row更新を含まない。
    def test_migration_contains_no_data_operation(self):
        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration(
            "linerichmenus", "0002_resource_replacement_operation"
        )
        self.assertEqual(
            {operation.__class__.__name__ for operation in migration.operations},
            {"AddField", "AddConstraint"},
        )
