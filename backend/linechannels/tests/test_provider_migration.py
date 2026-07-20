from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ProviderMigrationTests(TransactionTestCase):
    migrate_from = [("linechannels", "0001_initial")]
    migrate_to = [("linechannels", "0002_linechannel_provider_id")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        channel_model = old_apps.get_model("linechannels", "LineChannel")
        credential_model = old_apps.get_model(
            "linechannels", "LineChannelCredential"
        )
        self.public_id = uuid4()
        channel = channel_model.objects.create(
            public_id=self.public_id,
            messaging_api_channel_id="1234567890",
            bot_user_id=f"U{uuid4().hex}",
            label="既存チャネル",
            is_active=True,
        )
        credential_model.objects.create(
            line_channel=channel,
            access_token_ciphertext=b"encrypted-access",
            channel_secret_ciphertext=b"encrypted-secret",
        )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    # テストケース: provider追加前の既存チャネルとcredentialをnullable migrationへ進める。
    # 期待値: 表示情報とcredential参照を維持し、legacy providerは未設定のまま識別できる。
    def test_nullable_provider_migration_preserves_legacy_channel_and_credentials(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        channel_model = apps.get_model("linechannels", "LineChannel")
        credential_model = apps.get_model(
            "linechannels", "LineChannelCredential"
        )

        channel = channel_model.objects.get(public_id=self.public_id)
        credential = credential_model.objects.get(line_channel_id=channel.id)

        self.assertEqual(channel.label, "既存チャネル")
        self.assertTrue(channel.is_active)
        self.assertIsNone(channel.provider_id)
        self.assertEqual(bytes(credential.access_token_ciphertext), b"encrypted-access")
        self.assertEqual(bytes(credential.channel_secret_ciphertext), b"encrypted-secret")

    # テストケース: provider追加後のschemaを旧migrationへ戻す。
    # 期待値: nullable列と索引だけが除去され、既存チャネルとcredentialは保持される。
    def test_provider_migration_is_backwards_compatible_for_legacy_rows(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        channel_model = new_apps.get_model("linechannels", "LineChannel")
        channel_model.objects.filter(public_id=self.public_id).update(
            provider_id=None
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        old_channel_model = old_apps.get_model("linechannels", "LineChannel")
        old_credential_model = old_apps.get_model(
            "linechannels", "LineChannelCredential"
        )

        channel = old_channel_model.objects.get(public_id=self.public_id)
        self.assertEqual(channel.label, "既存チャネル")
        self.assertTrue(
            old_credential_model.objects.filter(line_channel_id=channel.id).exists()
        )
