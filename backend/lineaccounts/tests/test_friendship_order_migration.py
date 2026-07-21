from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class FriendshipOrderMigrationTests(TransactionTestCase):
    migrate_from = [("lineaccounts", "0001_initial")]
    migrate_to = [("lineaccounts", "0002_friendship_order")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        channel_model = old_apps.get_model("linechannels", "LineChannel")
        identity_model = old_apps.get_model("lineaccounts", "LineIdentity")
        recipient_model = old_apps.get_model("lineaccounts", "DeliveryRecipient")
        channel = channel_model.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label="既存チャネル",
            provider_id="0012345678",
            is_active=False,
        )
        identity = identity_model.objects.create(
            provider_id="0012345678",
            subject=f"U{uuid4().hex}",
            display_name="既存利用者",
        )
        recipient = recipient_model.objects.create(
            identity=identity,
            line_channel=channel,
            enabled=False,
            friendship_state="friend",
        )
        self.recipient_public_id = recipient.public_id
        self.created_at = recipient.created_at

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    # テストケース: order metadata追加前の既存recipientを新schemaへ移行する
    # 期待値: 状態・enabled・登録時刻を維持し、新しいorder pairをnullで開始する
    def test_migration_preserves_existing_recipient_and_starts_with_null_order(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        recipient_model = apps.get_model("lineaccounts", "DeliveryRecipient")

        recipient = recipient_model.objects.get(public_id=self.recipient_public_id)
        self.assertEqual(recipient.friendship_state, "friend")
        self.assertFalse(recipient.enabled)
        self.assertEqual(recipient.created_at, self.created_at)
        self.assertIsNone(recipient.last_friendship_event_occurred_at_ms)
        self.assertIsNone(recipient.last_friendship_webhook_event_id)
