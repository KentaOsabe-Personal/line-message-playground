from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from typing import get_args
from uuid import uuid4

from django.apps import apps
from django.test import SimpleTestCase

from lineaccounts.types import LineSubject
from linewebhooks.types import HandlerSucceeded, VerifiedWebhookEvent

from linefriendships.types import (
    AccountProjectionRepository,
    FriendshipAuditRecord,
    FriendshipAuditRepository,
    FriendshipEventParser,
    FriendshipSyncHandler,
    InvalidFriendshipEvent,
    LockedRecipientProjection,
    OutOfScopeSource,
    ProjectionOutcome,
    ProjectionTargetMissing,
    ValidatedFriendshipEvent,
)


class _Parser:
    def parse(self, event):
        return InvalidFriendshipEvent()


class _Handler:
    def handle(self, event):
        return HandlerSucceeded()


class _AccountRepository:
    def lock_target(self, *, channel_public_id, provider_id, subject):
        return ProjectionTargetMissing()

    def apply_locked(
        self,
        target,
        *,
        friendship_state,
        occurred_at_ms,
        webhook_event_id,
    ):
        return None


class _AuditRepository:
    def record(self, audit):
        return None


class FriendshipDomainTypeTests(SimpleTestCase):
    # テストケース: 検証済み友だちイベントへLINE user ID canaryを格納する
    # 期待値: immutableな値として保持し、reprへsubjectの生値を露出しない
    def test_validated_event_is_frozen_and_has_safe_repr(self):
        canary = "U" + "a" * 32
        event = ValidatedFriendshipEvent(
            channel_public_id=uuid4(),
            webhook_event_id="01J00000000000000000000000",
            event_type="follow",
            occurred_at_ms=1,
            subject=LineSubject(canary),
            target_state="friend",
            is_unblocked=False,
        )

        self.assertNotIn(canary, repr(event))
        with self.assertRaises(FrozenInstanceError):
            event.target_state = "not_friend"  # type: ignore[misc]

    # テストケース: 友だち同期のsafe outcome集合を参照する
    # 期待値: 設計で許可された8分類だけを公開する
    def test_projection_outcome_contains_only_safe_choices(self):
        self.assertEqual(
            set(get_args(ProjectionOutcome)),
            {
                "applied",
                "state_maintained",
                "stale",
                "duplicate",
                "unlinked",
                "unresolvable",
                "out_of_scope",
                "invalid",
            },
        )

    # テストケース: locked projectionとsafe audit recordを生成する
    # 期待値: 値を凍結し、audit契約にPII fieldを含めない
    def test_projection_and_audit_values_are_frozen_and_pii_free(self):
        projection = LockedRecipientProjection(
            recipient_public_id=uuid4(),
            registered_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
            friendship_state="unknown",
            last_occurred_at_ms=None,
            last_webhook_event_id=None,
        )
        audit = FriendshipAuditRecord(
            channel_public_id=uuid4(),
            webhook_event_id="01J00000000000000000000000",
            event_type="follow",
            occurred_at_ms=1,
            outcome="applied",
            is_unblocked=True,
        )

        self.assertEqual(
            {field.name for field in fields(audit)},
            {
                "channel_public_id",
                "webhook_event_id",
                "event_type",
                "occurred_at_ms",
                "outcome",
                "is_unblocked",
            },
        )
        with self.assertRaises(FrozenInstanceError):
            projection.friendship_state = "friend"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            audit.outcome = "stale"  # type: ignore[misc]

    # テストケース: 不正な時刻・order pair・unfollow補助flagを共有値へ渡す
    # 期待値: domain境界で安全なValueErrorとして拒否する
    def test_shared_values_enforce_integrity_invariants(self):
        with self.assertRaises(ValueError):
            ValidatedFriendshipEvent(
                channel_public_id=uuid4(),
                webhook_event_id="01J00000000000000000000000",
                event_type="follow",
                occurred_at_ms=-1,
                subject=LineSubject("U" + "b" * 32),
                target_state="friend",
                is_unblocked=None,
            )
        with self.assertRaises(ValueError):
            LockedRecipientProjection(
                recipient_public_id=uuid4(),
                registered_at=datetime.now(timezone.utc),
                friendship_state="unknown",
                last_occurred_at_ms=1,
                last_webhook_event_id=None,
            )
        with self.assertRaises(ValueError):
            FriendshipAuditRecord(
                channel_public_id=uuid4(),
                webhook_event_id="01J00000000000000000000000",
                event_type="unfollow",
                occurred_at_ms=1,
                outcome="applied",
                is_unblocked=False,
            )

    # テストケース: parser・handler・account・auditの構造的実装を検査する
    # 期待値: 4つのruntime-checkable port契約へ適合する
    def test_ports_are_runtime_checkable(self):
        self.assertIsInstance(_Parser(), FriendshipEventParser)
        self.assertIsInstance(_Handler(), FriendshipSyncHandler)
        self.assertIsInstance(_AccountRepository(), AccountProjectionRepository)
        self.assertIsInstance(_AuditRepository(), FriendshipAuditRepository)

    # テストケース: 標準BackendのDjango app registryを参照する
    # 期待値: linefriendships appがruntimeとmigrationで共通利用できる
    def test_app_is_installed_in_standard_backend(self):
        self.assertEqual(apps.get_app_config("linefriendships").name, "linefriendships")
