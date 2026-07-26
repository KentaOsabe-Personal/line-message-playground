import uuid
from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class LinkedRecipientDeliveryMigrationTests(TransactionTestCase):
    migrate_from = [
        ("delivery", "0001_initial"),
        ("lineaccounts", "0002_friendship_order"),
    ]
    migrate_to = [
        ("delivery", "0002_linked_recipient_delivery"),
        ("lineaccounts", "0002_friendship_order"),
    ]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        identity_model = old_apps.get_model("lineaccounts", "LineIdentity")
        owner_model = old_apps.get_model("lineaccounts", "OwnerAccount")
        attempt_model = old_apps.get_model("delivery", "DeliveryAttempt")

        identity = identity_model.objects.create(
            provider_id="0012345678",
            subject=f"U{uuid.uuid4().hex}",
            display_name="移行時のowner",
        )
        owner_model.objects.update_or_create(
            slot=1,
            defaults={
                "state": "active",
                "identity_id": identity.id,
            },
        )
        self.owner_identity_public_id = identity.public_id

        accepted_at = timezone.now().replace(microsecond=123456)
        self.legacy_rows = []
        legacy_values = (
            {
                "status": "processing",
                "active_content_fingerprint": "1" * 64,
            },
            {
                "status": "succeeded",
                "active_content_fingerprint": None,
                "line_request_id": "line-request-legacy",
                "line_accepted_request_id": "line-accepted-request-legacy",
                "sent_at": accepted_at + timedelta(seconds=1),
                "completed_at": accepted_at + timedelta(seconds=1),
            },
            {
                "status": "failed",
                "active_content_fingerprint": None,
                "failure_type": "invalid_request",
                "failed_at": accepted_at + timedelta(seconds=2),
                "completed_at": accepted_at + timedelta(seconds=2),
            },
            {
                "status": "unknown",
                "active_content_fingerprint": None,
                "failure_type": "timeout_unknown",
                "failed_at": accepted_at + timedelta(seconds=3),
                "completed_at": accepted_at + timedelta(seconds=3),
            },
        )
        for index, overrides in enumerate(legacy_values, start=1):
            fingerprint = str(index) * 64
            operation_id = uuid.uuid4()
            values = {
                "operation_id": operation_id,
                "subject": f"件名{index}",
                "body": f"本文{index}",
                "formatted_text": f"【件名{index}】\n\n本文{index}",
                "content_fingerprint": fingerprint,
                "accepted_at": accepted_at,
                "processing_expires_at": accepted_at + timedelta(seconds=30),
                **overrides,
            }
            attempt_model.objects.create(**values)
            self.legacy_rows.append(values)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    # テストケース: active ownerが一意な0001のfixed配信を新schemaへforward migrationする。
    # 期待値: 全監査値を同値で保持し、slot 1とrequest fingerprint、identity snapshotを補完する。
    def test_forward_migration_preserves_all_legacy_values_and_backfills_owner_scope(
        self,
    ):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")

        self.assertEqual(attempt_model.objects.count(), len(self.legacy_rows))
        for expected in self.legacy_rows:
            with self.subTest(status=expected["status"]):
                attempt = attempt_model.objects.get(
                    operation_id=expected["operation_id"]
                )
                for field_name in (
                    "operation_id",
                    "subject",
                    "body",
                    "formatted_text",
                    "content_fingerprint",
                    "active_content_fingerprint",
                    "status",
                    "failure_type",
                    "line_request_id",
                    "line_accepted_request_id",
                    "accepted_at",
                    "processing_expires_at",
                    "sent_at",
                    "failed_at",
                    "completed_at",
                ):
                    self.assertEqual(
                        getattr(attempt, field_name),
                        expected.get(field_name),
                    )
                self.assertEqual(
                    attempt.request_fingerprint,
                    expected["content_fingerprint"],
                )
                self.assertEqual(attempt.owner_principal_slot, 1)
                self.assertEqual(
                    attempt.owner_identity_public_id,
                    self.owner_identity_public_id,
                )
                self.assertEqual(attempt.target_mode, "fixed_user")
                self.assertFalse(attempt.receipt_requested)

    # テストケース: owner identityを解決できない状態で0001のfixed配信をforward migrationする。
    # 期待値: 行を削除せずslot 1で参照可能にし、監査identity snapshotだけをnullのまま保つ。
    def test_forward_migration_keeps_legacy_rows_when_identity_is_unresolved(self):
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        owner_model = old_apps.get_model("lineaccounts", "OwnerAccount")
        owner_model.objects.filter(slot=1).update(
            state="vacant",
            identity_id=None,
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")

        self.assertEqual(attempt_model.objects.count(), len(self.legacy_rows))
        self.assertFalse(
            attempt_model.objects.exclude(owner_principal_slot=1).exists()
        )
        self.assertFalse(
            attempt_model.objects.exclude(
                owner_identity_public_id__isnull=True
            ).exists()
        )

    # テストケース: linked rowがないforward済みschemaを0001へrollbackする。
    # 期待値: fixed行の件数、operation ID、message、fingerprint、結果、LINE ID、時刻を同値で保持する。
    def test_reverse_migration_preserves_fixed_legacy_values(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        apps = self.executor.loader.project_state(self.migrate_from).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")

        self.assertEqual(attempt_model.objects.count(), len(self.legacy_rows))
        for expected in self.legacy_rows:
            with self.subTest(status=expected["status"]):
                attempt = attempt_model.objects.get(
                    operation_id=expected["operation_id"]
                )
                for field_name in (
                    "operation_id",
                    "subject",
                    "body",
                    "formatted_text",
                    "content_fingerprint",
                    "active_content_fingerprint",
                    "status",
                    "failure_type",
                    "line_request_id",
                    "line_accepted_request_id",
                    "accepted_at",
                    "processing_expires_at",
                    "sent_at",
                    "failed_at",
                    "completed_at",
                ):
                    self.assertEqual(
                        getattr(attempt, field_name),
                        expected.get(field_name),
                    )

    # テストケース: forward後にlinked recipient配信が存在するschemaを0001へrollbackする。
    # 期待値: linked行をfixedへ変換・削除せず、明示的な例外でrollbackを停止する。
    def test_reverse_migration_stops_when_linked_rows_exist(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")
        completed_at = timezone.now()
        attempt_model.objects.create(
            operation_id=uuid.uuid4(),
            subject="linked件名",
            body="linked本文",
            formatted_text="【linked件名】\n\nlinked本文",
            content_fingerprint="a" * 64,
            active_content_fingerprint=None,
            request_fingerprint="b" * 64,
            active_request_fingerprint=None,
            target_mode="linked_recipient",
            owner_principal_slot=1,
            owner_identity_public_id=uuid.uuid4(),
            channel_public_id=uuid.uuid4(),
            channel_label_snapshot="移行後チャネル",
            recipient_public_id=uuid.uuid4(),
            channel_active_snapshot=True,
            recipient_enabled_snapshot=True,
            friendship_state_snapshot="friend",
            status="succeeded",
            accepted_at=completed_at - timedelta(seconds=1),
            processing_expires_at=completed_at + timedelta(seconds=29),
            sent_at=completed_at,
            completed_at=completed_at,
        )

        self.executor = MigrationExecutor(connection)
        with self.assertRaisesMessage(
            RuntimeError,
            "linked recipient delivery rows exist",
        ):
            self.executor.migrate(self.migrate_from)

        self.assertTrue(
            attempt_model.objects.filter(target_mode="linked_recipient").exists()
        )
