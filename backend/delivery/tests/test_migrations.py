import uuid
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
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

    def latest_attempt_values(self, **overrides):
        accepted_at = timezone.now()
        values = {
            "operation_id": uuid.uuid4(),
            "owner_principal_slot": 1,
            "subject": "移行後件名",
            "body": "移行後本文",
            "formatted_text": "【移行後件名】\n\n移行後本文",
            "content_fingerprint": "f" * 64,
            "active_content_fingerprint": "f" * 64,
            "request_fingerprint": "f" * 64,
            "target_mode": "fixed_user",
            "status": "processing",
            "accepted_at": accepted_at,
            "processing_expires_at": accepted_at + timedelta(seconds=30),
        }
        values.update(overrides)
        return values

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

    # テストケース: 0001からforward、rollback、再forwardを同じfixed配信群へ順に適用する。
    # 期待値: 各往復後も件数と全legacy監査値が同値で、owner scopeのbackfillが再現する。
    def test_forward_rollback_and_reapply_are_reproducible_for_fixed_rows(self):
        for migration_target in (
            self.migrate_to,
            self.migrate_from,
            self.migrate_to,
        ):
            self.executor = MigrationExecutor(connection)
            self.executor.migrate(migration_target)
            apps = self.executor.loader.project_state(migration_target).apps
            attempt_model = apps.get_model("delivery", "DeliveryAttempt")

            self.assertEqual(attempt_model.objects.count(), len(self.legacy_rows))
            for expected in self.legacy_rows:
                with self.subTest(
                    migration=migration_target[0][1],
                    status=expected["status"],
                ):
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
                    if migration_target == self.migrate_to:
                        self.assertEqual(attempt.target_mode, "fixed_user")
                        self.assertEqual(attempt.owner_principal_slot, 1)
                        self.assertEqual(
                            attempt.request_fingerprint,
                            expected["content_fingerprint"],
                        )

    # テストケース: migration後の実DBへsnapshot不足のlinked recipient行を直接保存する。
    # 期待値: delivery_attempt_valid_target制約が不完全な対象監査を拒否する。
    def test_forward_migration_installs_target_mode_constraint(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")
        accepted_at = timezone.now()

        with self.assertRaises(IntegrityError), transaction.atomic():
            attempt_model.objects.create(
                operation_id=uuid.uuid4(),
                subject="不完全なlinked件名",
                body="不完全なlinked本文",
                formatted_text="【不完全なlinked件名】\n\n不完全なlinked本文",
                content_fingerprint="a" * 64,
                active_content_fingerprint=None,
                request_fingerprint="b" * 64,
                active_request_fingerprint="b" * 64,
                target_mode="linked_recipient",
                owner_principal_slot=1,
                status="processing",
                accepted_at=accepted_at,
                processing_expires_at=accepted_at + timedelta(seconds=30),
            )

    # テストケース: migration後の実DBへ不整合なprocessing終端値を直接保存する。
    # 期待値: delivery_attempt_valid_state制約がactive fingerprintのないprocessing行を拒否する。
    def test_forward_migration_installs_status_constraint(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")
        accepted_at = timezone.now()

        with self.assertRaises(IntegrityError), transaction.atomic():
            attempt_model.objects.create(
                operation_id=uuid.uuid4(),
                subject="不整合な状態",
                body="本文",
                formatted_text="【不整合な状態】\n\n本文",
                content_fingerprint="c" * 64,
                active_content_fingerprint=None,
                request_fingerprint="c" * 64,
                target_mode="fixed_user",
                owner_principal_slot=1,
                status="processing",
                accepted_at=accepted_at,
                processing_expires_at=accepted_at + timedelta(seconds=30),
            )

    # テストケース: migration後の実DBへ受取確認なしだがcapability digestを持つfixed行を保存する。
    # 期待値: delivery_attempt_valid_receipt制約がreceipt列の不整合を拒否する。
    def test_forward_migration_installs_receipt_constraint(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")
        accepted_at = timezone.now()

        with self.assertRaises(IntegrityError), transaction.atomic():
            attempt_model.objects.create(
                operation_id=uuid.uuid4(),
                subject="受取確認不整合",
                body="本文",
                formatted_text="【受取確認不整合】\n\n本文",
                content_fingerprint="d" * 64,
                active_content_fingerprint="d" * 64,
                request_fingerprint="d" * 64,
                target_mode="fixed_user",
                owner_principal_slot=1,
                status="processing",
                accepted_at=accepted_at,
                processing_expires_at=accepted_at + timedelta(seconds=30),
                receipt_requested=False,
                receipt_token_digest="e" * 64,
            )

    # テストケース: 0002適用後も従来形式のfixed processing配信を実DBへ新規保存する。
    # 期待値: linked snapshotやactive request fingerprintなしで保存でき、fixed配信契約を維持する。
    def test_forward_migration_keeps_fixed_delivery_write_contract(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")
        accepted_at = timezone.now()
        operation_id = uuid.uuid4()

        attempt_model.objects.create(
            operation_id=operation_id,
            owner_principal_slot=1,
            subject="移行後fixed件名",
            body="移行後fixed本文",
            formatted_text="【移行後fixed件名】\n\n移行後fixed本文",
            content_fingerprint="f" * 64,
            active_content_fingerprint="f" * 64,
            request_fingerprint="f" * 64,
            target_mode="fixed_user",
            status="processing",
            accepted_at=accepted_at,
            processing_expires_at=accepted_at + timedelta(seconds=30),
        )

        attempt = attempt_model.objects.get(operation_id=operation_id)
        self.assertEqual(attempt.target_mode, "fixed_user")
        self.assertEqual(attempt.owner_principal_slot, 1)
        self.assertEqual(attempt.request_fingerprint, "f" * 64)
        self.assertEqual(attempt.active_content_fingerprint, "f" * 64)
        self.assertIsNone(attempt.active_request_fingerprint)
        self.assertIsNone(attempt.channel_public_id)
        self.assertIsNone(attempt.recipient_public_id)

    # テストケース: migration後のfixed行でowner slotまたはrequest fingerprintを欠落させる。
    # 期待値: modeに関係しない二つの必須列がMySQLのNOT NULL制約でそれぞれ拒否される。
    def test_forward_migration_requires_owner_scope_and_request_fingerprint(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")

        for field_name in ("owner_principal_slot", "request_fingerprint"):
            with self.subTest(field_name=field_name):
                values = self.latest_attempt_values(
                    operation_id=uuid.uuid4(),
                    content_fingerprint=uuid.uuid4().hex * 2,
                    active_content_fingerprint=uuid.uuid4().hex * 2,
                    request_fingerprint=uuid.uuid4().hex * 2,
                )
                values[field_name] = None
                with self.assertRaises(IntegrityError), transaction.atomic():
                    attempt_model.objects.create(**values)

    # テストケース: migration後の実DBへ完全なfixed行とlinked recipient行を保存する。
    # 期待値: 両modeの正例が保存でき、unknown modeだけが制約で拒否される。
    def test_forward_migration_accepts_known_modes_and_rejects_unknown_mode(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")
        fixed_values = self.latest_attempt_values()
        linked_fingerprint = "1" * 64
        linked_values = self.latest_attempt_values(
            operation_id=uuid.uuid4(),
            content_fingerprint=linked_fingerprint,
            active_content_fingerprint=None,
            request_fingerprint=linked_fingerprint,
            active_request_fingerprint=linked_fingerprint,
            target_mode="linked_recipient",
            owner_identity_public_id=uuid.uuid4(),
            channel_public_id=uuid.uuid4(),
            channel_label_snapshot="移行後チャネル",
            recipient_public_id=uuid.uuid4(),
            channel_active_snapshot=True,
            recipient_enabled_snapshot=True,
            friendship_state_snapshot="friend",
        )

        fixed = attempt_model.objects.create(**fixed_values)
        linked = attempt_model.objects.create(**linked_values)

        self.assertEqual(fixed.target_mode, "fixed_user")
        self.assertEqual(linked.target_mode, "linked_recipient")
        unknown_values = {
            **linked_values,
            "operation_id": uuid.uuid4(),
            "content_fingerprint": "2" * 64,
            "request_fingerprint": "2" * 64,
            "active_request_fingerprint": "2" * 64,
            "target_mode": "unknown_mode",
        }
        with self.assertRaises(IntegrityError), transaction.atomic():
            attempt_model.objects.create(**unknown_values)

    # テストケース: migration後のprocessing、succeeded、failed、unknown各枝を一項目ずつ崩す。
    # 期待値: 狙った枝以外の必須値を満たしていてもstatus整合性制約が各不正行を拒否する。
    def test_forward_migration_rejects_each_invalid_status_branch(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")
        completed_at = timezone.now()
        invalid_cases = (
            {
                "status": "processing",
                "active_content_fingerprint": None,
            },
            {
                "status": "succeeded",
                "active_content_fingerprint": None,
                "sent_at": completed_at,
                "failed_at": completed_at,
                "completed_at": completed_at,
            },
            {
                "status": "failed",
                "active_content_fingerprint": None,
                "failure_type": "invalid_request",
                "sent_at": completed_at,
                "failed_at": completed_at,
                "completed_at": completed_at,
            },
            {
                "status": "unknown",
                "active_content_fingerprint": None,
                "failure_type": "timeout_unknown",
                "sent_at": completed_at,
                "failed_at": completed_at,
                "completed_at": completed_at,
            },
        )

        for index, overrides in enumerate(invalid_cases):
            with self.subTest(status=overrides["status"]):
                fingerprint = f"{index + 10:064x}"
                values = self.latest_attempt_values(
                    operation_id=uuid.uuid4(),
                    content_fingerprint=fingerprint,
                    request_fingerprint=fingerprint,
                    **(
                        {"active_content_fingerprint": fingerprint}
                        | overrides
                    ),
                )
                with self.assertRaises(IntegrityError), transaction.atomic():
                    attempt_model.objects.create(**values)

    # テストケース: migration後のreceipt requested正例とcommitment・確認pairの欠落を保存する。
    # 期待値: pending/confirmed正例だけを許可し、expiry/digest/confirmed/eventの各片側欠落を拒否する。
    def test_forward_migration_enforces_complete_receipt_contract(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        attempt_model = apps.get_model("delivery", "DeliveryAttempt")
        now = timezone.now()
        pending_values = self.latest_attempt_values(
            operation_id=uuid.uuid4(),
            content_fingerprint="3" * 64,
            active_content_fingerprint="3" * 64,
            request_fingerprint="3" * 64,
            receipt_requested=True,
            receipt_expires_at=now + timedelta(hours=24),
            receipt_token_digest="4" * 64,
        )
        confirmed_values = {
            **pending_values,
            "operation_id": uuid.uuid4(),
            "content_fingerprint": "5" * 64,
            "active_content_fingerprint": "5" * 64,
            "request_fingerprint": "5" * 64,
            "receipt_token_digest": "6" * 64,
            "receipt_confirmed_at": now,
            "receipt_webhook_event_id": "01J00000000000000000000000",
        }

        self.assertIsNotNone(attempt_model.objects.create(**pending_values).pk)
        self.assertIsNotNone(attempt_model.objects.create(**confirmed_values).pk)

        invalid_receipts = (
            {"receipt_requested": True, "receipt_token_digest": "7" * 64},
            {
                "receipt_requested": True,
                "receipt_expires_at": now + timedelta(hours=24),
            },
            {
                "receipt_requested": True,
                "receipt_expires_at": now + timedelta(hours=24),
                "receipt_token_digest": "8" * 64,
                "receipt_confirmed_at": now,
            },
            {
                "receipt_requested": True,
                "receipt_expires_at": now + timedelta(hours=24),
                "receipt_token_digest": "9" * 64,
                "receipt_webhook_event_id": "01J11111111111111111111111",
            },
        )
        for index, receipt_values in enumerate(invalid_receipts):
            with self.subTest(index=index):
                fingerprint = f"{index + 20:064x}"
                values = self.latest_attempt_values(
                    operation_id=uuid.uuid4(),
                    content_fingerprint=fingerprint,
                    active_content_fingerprint=fingerprint,
                    request_fingerprint=fingerprint,
                    **receipt_values,
                )
                with self.assertRaises(IntegrityError), transaction.atomic():
                    attempt_model.objects.create(**values)
