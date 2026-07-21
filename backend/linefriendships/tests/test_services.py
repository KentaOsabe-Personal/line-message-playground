from datetime import datetime, timezone
from uuid import uuid4

from django.test import SimpleTestCase, TransactionTestCase

from lineaccounts.types import LineSubject
from lineaccounts.friendship_repositories import DjangoAccountProjectionRepository
from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerAccount
from linechannels.models import LineChannel
from linechannels.repositories import DjangoLineChannelDirectory
from linewebhooks.types import (
    FrozenJsonObject,
    HandlerFailed,
    HandlerSucceeded,
    VerifiedWebhookEvent,
)

from linefriendships.models import FriendshipSyncAudit
from linefriendships.parsing import DefaultFriendshipEventParser
from linefriendships.repositories import DjangoFriendshipAuditRepository
from linefriendships.services import DefaultFriendshipSyncService, decide_projection
from linefriendships.types import LockedRecipientProjection, ValidatedFriendshipEvent


class FriendshipOrderingDecisionTests(SimpleTestCase):
    baseline_ms = int(
        datetime(2026, 7, 21, 0, 0, 0, 999999, tzinfo=timezone.utc).timestamp()
        * 1000
    )

    def target(
        self,
        *,
        state="unknown",
        last_occurred_at_ms=None,
        last_webhook_event_id=None,
    ):
        return LockedRecipientProjection(
            recipient_public_id=uuid4(),
            registered_at=datetime(
                2026, 7, 21, 0, 0, 0, 999999, tzinfo=timezone.utc
            ),
            friendship_state=state,
            last_occurred_at_ms=last_occurred_at_ms,
            last_webhook_event_id=last_webhook_event_id,
        )

    def event(
        self,
        *,
        occurred_at_ms,
        webhook_event_id="01J00000000000000000000000",
        target_state="friend",
    ):
        return ValidatedFriendshipEvent(
            channel_public_id=uuid4(),
            webhook_event_id=webhook_event_id,
            event_type=("follow" if target_state == "friend" else "unfollow"),
            occurred_at_ms=occurred_at_ms,
            subject=LineSubject("U" + "a" * 32),
            target_state=target_state,
            is_unblocked=None,
        )

    # テストケース: 登録時刻のミリ秒floor以下と直後のeventを比較する
    # 期待値: baseline以下はstale、baseline直後は適用と判定する
    def test_enforces_registration_baseline(self):
        target = self.target()

        self.assertEqual(
            decide_projection(target, self.event(occurred_at_ms=self.baseline_ms)),
            "stale",
        )
        self.assertEqual(
            decide_projection(target, self.event(occurred_at_ms=self.baseline_ms + 1)),
            "applied",
        )

    # テストケース: 最終event IDと同じeventを再提示する
    # 期待値: 追加の状態/order変更を行わないduplicateと判定する
    def test_classifies_same_event_id_as_duplicate(self):
        target = self.target(
            state="friend",
            last_occurred_at_ms=self.baseline_ms + 2_000,
            last_webhook_event_id="01J00000000000000000000009",
        )

        self.assertEqual(
            decide_projection(
                target,
                self.event(
                    occurred_at_ms=self.baseline_ms + 2_000,
                    webhook_event_id="01J00000000000000000000009",
                ),
            ),
            "duplicate",
        )

    # テストケース: 最終order keyより古い時刻と同時刻の小さいIDを比較する
    # 期待値: 到着順に関係なく両方をstaleと判定する
    def test_rejects_older_order_keys(self):
        target = self.target(
            last_occurred_at_ms=self.baseline_ms + 3_000,
            last_webhook_event_id="01J0000000000000000000000B",
        )
        events = (
            self.event(
                occurred_at_ms=self.baseline_ms + 2_999,
                webhook_event_id="01J0000000000000000000000Z",
            ),
            self.event(
                occurred_at_ms=self.baseline_ms + 3_000,
                webhook_event_id="01J0000000000000000000000A",
            ),
        )

        for event in events:
            with self.subTest(event_id=event.webhook_event_id):
                self.assertEqual(decide_projection(target, event), "stale")

    # テストケース: 同時刻でASCII辞書順が大きいIDの反対状態を比較する
    # 期待値: より新しいkeyとして状態とorderの適用を判定する
    def test_uses_event_id_as_same_timestamp_tie_breaker(self):
        target = self.target(
            state="friend",
            last_occurred_at_ms=self.baseline_ms + 3_000,
            last_webhook_event_id="01J0000000000000000000000A",
        )

        self.assertEqual(
            decide_projection(
                target,
                self.event(
                    occurred_at_ms=self.baseline_ms + 3_000,
                    webhook_event_id="01J0000000000000000000000B",
                    target_state="not_friend",
                ),
            ),
            "applied",
        )

    # テストケース: 現在と同じ状態を示す新しいeventを比較する
    # 期待値: 状態を維持しつつorderを前進させる分類を返す
    def test_advances_order_for_newer_same_state_event(self):
        target = self.target(
            state="friend",
            last_occurred_at_ms=self.baseline_ms + 3_000,
            last_webhook_event_id="01J0000000000000000000000A",
        )

        self.assertEqual(
            decide_projection(
                target,
                self.event(
                    occurred_at_ms=self.baseline_ms + 4_000,
                    webhook_event_id="01J00000000000000000000001",
                ),
            ),
            "state_maintained",
        )

    # テストケース: unknownまたは反対状態へ新しいeventを比較する
    # 期待値: eventのtarget stateへ収束するappliedを返す
    def test_applies_newer_event_from_any_different_state(self):
        for current_state, target_state in (
            ("unknown", "friend"),
            ("unknown", "not_friend"),
            ("friend", "not_friend"),
            ("not_friend", "friend"),
        ):
            with self.subTest(current_state=current_state, target_state=target_state):
                self.assertEqual(
                    decide_projection(
                        self.target(state=current_state),
                        self.event(
                            occurred_at_ms=self.baseline_ms + 1,
                            target_state=target_state,
                        ),
                    ),
                    "applied",
                )


class _MissingChannelDirectory:
    def get(self, public_id):
        return None


class _FailingAuditRepository:
    def record(self, audit):
        raise RuntimeError("safe audit failure")


class _FailingAccountRepository:
    def lock_target(self, **kwargs):
        raise RuntimeError("safe account failure")

    def apply_locked(self, target, **kwargs):
        raise AssertionError("unreachable")


class FriendshipSyncServiceTests(TransactionTestCase):
    def setUp(self):
        self.provider_id = "0012345678"
        self.subject_value = "U" + "a" * 32
        self.identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=self.subject_value,
            display_name="Owner",
        )
        OwnerAccount.objects.get_or_create(slot=1)
        OwnerAccount.objects.filter(slot=1).update(
            state=OwnerAccount.State.ACTIVE,
            identity=self.identity,
        )
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id="U" + uuid4().hex,
            label="通知チャネル",
            provider_id=self.provider_id,
            is_active=True,
        )
        self.recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.channel,
            enabled=False,
            friendship_state="unknown",
        )
        self.parser = DefaultFriendshipEventParser()

    def service(self, *, directory=None, account=None, audit=None):
        return DefaultFriendshipSyncService(
            self.parser,
            directory or DjangoLineChannelDirectory(),
            account or DjangoAccountProjectionRepository(),
            audit or DjangoFriendshipAuditRepository(),
        )

    def event(
        self,
        *,
        event_type="follow",
        event_id="01J00000000000000000000000",
        occurred_at_ms=None,
        source=None,
        follow=None,
    ):
        occurred_at_ms = occurred_at_ms or (
            int(self.recipient.created_at.timestamp() * 1000) + 1
        )
        data = {
            "type": event_type,
            "source": source
            or {"type": "user", "userId": self.subject_value},
        }
        if follow is not None:
            data["follow"] = follow
        return VerifiedWebhookEvent(
            channel_public_id=self.channel.public_id,
            webhook_event_id=event_id,
            event_type=event_type,
            occurred_at_ms=occurred_at_ms,
            is_redelivery=False,
            data=FrozenJsonObject(data),
        )

    # テストケース: exact matchする新しいfollowを同期handlerへ渡す
    # 期待値: state/orderとapplied監査を同一成功処理で確定する
    def test_applies_projection_and_records_safe_audit(self):
        result = self.service().handle(
            self.event(follow={"isUnblocked": True})
        )

        self.assertIsInstance(result, HandlerSucceeded)
        stored = DeliveryRecipient.objects.get(pk=self.recipient.pk)
        self.assertEqual(stored.friendship_state, "friend")
        self.assertEqual(
            stored.last_friendship_webhook_event_id,
            "01J00000000000000000000000",
        )
        audit = FriendshipSyncAudit.objects.get()
        self.assertEqual(audit.outcome, "applied")
        self.assertIs(audit.is_unblocked, True)
        self.assertFalse(stored.enabled)

    # テストケース: 同状態の新event・同一ID・古い反対eventを順に処理する
    # 期待値: state_maintained・duplicate・staleを正常監査し最新状態を維持する
    def test_treats_maintained_duplicate_and_stale_as_success(self):
        first = self.service().handle(self.event())
        maintained = self.service().handle(
            self.event(
                event_id="01J00000000000000000000001",
                occurred_at_ms=int(self.recipient.created_at.timestamp() * 1000)
                + 2,
            )
        )
        duplicate = self.service().handle(
            self.event(
                event_id="01J00000000000000000000001",
                occurred_at_ms=int(self.recipient.created_at.timestamp() * 1000)
                + 2,
            )
        )
        stale = self.service().handle(
            self.event(
                event_type="unfollow",
                event_id="01J00000000000000000000009",
                occurred_at_ms=int(self.recipient.created_at.timestamp() * 1000)
                + 1,
            )
        )

        self.assertIsInstance(first, HandlerSucceeded)
        self.assertIsInstance(maintained, HandlerSucceeded)
        self.assertIsInstance(duplicate, HandlerSucceeded)
        self.assertIsInstance(stale, HandlerSucceeded)
        self.assertEqual(
            list(
                FriendshipSyncAudit.objects.order_by("pk").values_list(
                    "outcome", flat=True
                )
            ),
            ["applied", "state_maintained", "duplicate", "stale"],
        )
        self.assertEqual(
            DeliveryRecipient.objects.get(pk=self.recipient.pk).friendship_state,
            "friend",
        )

    # テストケース: 不正sourceとgroup sourceを同期handlerへ渡す
    # 期待値: recipientを変更せずinvalid/out_of_scopeを正常監査する
    def test_audits_parser_non_update_results_as_success(self):
        invalid = self.service().handle(self.event(source={"type": "user"}))
        out_of_scope = self.service().handle(
            self.event(
                event_id="01J00000000000000000000001",
                source={"type": "group", "groupId": "redacted"},
            )
        )

        self.assertIsInstance(invalid, HandlerSucceeded)
        self.assertIsInstance(out_of_scope, HandlerSucceeded)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.friendship_state, "unknown")
        self.assertEqual(
            list(
                FriendshipSyncAudit.objects.order_by("pk").values_list(
                    "outcome", flat=True
                )
            ),
            ["invalid", "out_of_scope"],
        )

    # テストケース: provider付きchannelまたはaccount targetを解決できない
    # 期待値: unresolvable/unlinkedを正常監査してrecipientを変更しない
    def test_audits_unresolvable_and_unlinked_as_success(self):
        unresolvable = self.service(directory=_MissingChannelDirectory()).handle(
            self.event()
        )
        unlinked = self.service().handle(
            self.event(
                event_id="01J00000000000000000000001",
                source={"type": "user", "userId": "U" + "b" * 32},
            )
        )

        self.assertIsInstance(unresolvable, HandlerSucceeded)
        self.assertIsInstance(unlinked, HandlerSucceeded)
        self.assertEqual(
            list(
                FriendshipSyncAudit.objects.order_by("pk").values_list(
                    "outcome", flat=True
                )
            ),
            ["unresolvable", "unlinked"],
        )

    # テストケース: state/order適用後に監査insertが失敗する
    # 期待値: transaction全体をrollbackしてsafeなHandlerFailedを返す
    def test_rolls_back_projection_when_audit_fails(self):
        result = self.service(audit=_FailingAuditRepository()).handle(self.event())

        self.assertIsInstance(result, HandlerFailed)
        stored = DeliveryRecipient.objects.get(pk=self.recipient.pk)
        self.assertEqual(stored.friendship_state, "unknown")
        self.assertIsNone(stored.last_friendship_event_occurred_at_ms)
        self.assertIsNone(stored.last_friendship_webhook_event_id)
        self.assertEqual(FriendshipSyncAudit.objects.count(), 0)

    # テストケース: account lockがstorage/contract failureを返す
    # 期待値: exception detailを公開せずHandlerFailedへ縮約する
    def test_returns_handler_failed_for_account_failure(self):
        result = self.service(account=_FailingAccountRepository()).handle(self.event())

        self.assertEqual(result, HandlerFailed())
        self.assertNotIn("account failure", repr(result))
