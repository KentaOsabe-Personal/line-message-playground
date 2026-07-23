from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import TestCase

from lineinteractions.models import InteractionAudit
from lineinteractions.types import InteractionAuditRecord


class InteractionAuditModelTests(TestCase):
    def valid_values(self, **overrides):
        values = {
            "channel_public_id": uuid4(),
            "webhook_event_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "event_type": "message",
            "operation_kind": "command",
            "operation_identifier": "connectivity_ping_v1",
            "interaction_outcome": "command_processed",
            "reply_outcome": "accepted",
        }
        values.update(overrides)
        return values

    # テストケース: interaction audit schemaのfield whitelistを調べる
    # 期待値: PII・token・cross-app FKを含まない安全fieldだけを持つ
    def test_schema_contains_only_safe_fields(self):
        self.assertEqual(
            {field.name for field in InteractionAudit._meta.fields},
            {
                "id",
                "channel_public_id",
                "webhook_event_id",
                "event_type",
                "operation_kind",
                "operation_identifier",
                "interaction_outcome",
                "reply_outcome",
                "recorded_at",
            },
        )

    # テストケース: application unionとDB CHECKへ同じ監査組合せ表を渡す
    # 期待値: 全safe outcomeだけを両境界が受理し不正/NULL/空値を両方が拒否する
    def test_application_and_database_accept_the_same_result_matrix(self):
        valid_overrides = [
            {"reply_outcome": reply}
            for reply in ("accepted", "rejected", "unknown")
        ]
        valid_overrides.extend(
            {
                "interaction_outcome": outcome,
                "reply_outcome": "not_started",
            }
            for outcome in (
                "processing_failed",
                "credential_unavailable",
                "deadline_exceeded",
            )
        )
        valid_overrides.extend(
            {
                "event_type": "postback",
                "operation_kind": "action",
                "operation_identifier": "confirm",
                "interaction_outcome": outcome,
                "reply_outcome": "not_started",
            }
            for outcome in (
                "action_succeeded",
                "action_no_change",
                "action_rejected",
                "handler_failed",
            )
        )
        valid_overrides.extend(
            {
                "event_type": event_type,
                "operation_kind": "none",
                "operation_identifier": None,
                "interaction_outcome": outcome,
                "reply_outcome": "not_started",
            }
            for event_type in ("message", "postback")
            for outcome in (
                "unknown",
                "invalid",
                "out_of_scope",
                "unlinked",
                "processing_failed",
            )
        )
        invalid_overrides = (
            {"operation_identifier": None},
            {"operation_identifier": ""},
            {"reply_outcome": "not_started"},
            {
                "interaction_outcome": "credential_unavailable",
                "reply_outcome": "accepted",
            },
            {"event_type": "postback"},
            {
                "event_type": "postback",
                "operation_kind": "action",
                "operation_identifier": None,
                "interaction_outcome": "action_succeeded",
                "reply_outcome": "not_started",
            },
            {
                "event_type": "postback",
                "operation_kind": "action",
                "operation_identifier": "",
                "interaction_outcome": "action_succeeded",
                "reply_outcome": "not_started",
            },
            {
                "event_type": "postback",
                "operation_kind": "action",
                "operation_identifier": "confirm",
                "interaction_outcome": "action_succeeded",
                "reply_outcome": "accepted",
            },
            {
                "operation_kind": "none",
                "operation_identifier": "unexpected",
                "interaction_outcome": "invalid",
                "reply_outcome": "not_started",
            },
            {
                "operation_kind": "none",
                "operation_identifier": None,
                "interaction_outcome": "invalid",
                "reply_outcome": "accepted",
            },
            {"event_type": "follow"},
        )

        for index, overrides in enumerate(valid_overrides):
            values = self.valid_values(
                webhook_event_id=f"{index:026d}",
                **overrides,
            )
            with self.subTest(valid=overrides):
                InteractionAuditRecord(**values)
                InteractionAudit.objects.create(**values)
        valid_count = len(valid_overrides)
        for offset, overrides in enumerate(invalid_overrides, start=valid_count):
            values = self.valid_values(
                webhook_event_id=f"{offset:026d}",
                **overrides,
            )
            with self.subTest(invalid=overrides):
                with self.assertRaises(ValueError):
                    InteractionAuditRecord(**values)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    InteractionAudit.objects.create(**values)

        self.assertEqual(InteractionAudit.objects.count(), valid_count)

    # テストケース: 同一webhook event IDの監査を二件保存する
    # 期待値: UNIQUE制約が重複を拒否する
    def test_rejects_duplicate_event_id(self):
        InteractionAudit.objects.create(**self.valid_values())

        with self.assertRaises(IntegrityError), transaction.atomic():
            InteractionAudit.objects.create(**self.valid_values())

    # テストケース: audit modelのindexとdb table契約を調べる
    # 期待値: 明示table名とchannel/time運用indexを持つ
    def test_table_and_index_contract(self):
        self.assertEqual(
            InteractionAudit._meta.db_table,
            "lineinteractions_interaction_audit",
        )
        self.assertIn(
            ("channel_public_id", "recorded_at"),
            {tuple(index.fields) for index in InteractionAudit._meta.indexes},
        )
