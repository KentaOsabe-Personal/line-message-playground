import base64
import hashlib
import hmac
import json
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase

from delivery.models import DeliveryAttempt
from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerAccount
from linechannels import runtime
from linechannels.crypto import FernetCredentialCipher
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.types import AccessToken, ChannelSecret, CredentialContext
from linefriendships.models import FriendshipSyncAudit
from linefriendships.repositories import DjangoFriendshipAuditRepository
from linewebhooks.models import WebhookEventReceipt


class SignedFriendshipProjectionIntegrationTests(TestCase):
    def setUp(self) -> None:
        runtime.load_credential_keyring()
        self.provider_id = "0012345678"
        self.subject = "U" + "a" * 32
        self.secret = "friendship-integration-secret"
        self.bot_user_id = "U" + "1" * 32
        self.identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=self.subject,
            display_name="Owner",
        )
        self.owner, _ = OwnerAccount.objects.get_or_create(slot=1)
        OwnerAccount.objects.filter(pk=self.owner.pk).update(
            state=OwnerAccount.State.ACTIVE,
            identity=self.identity,
        )
        self.owner.refresh_from_db()
        self.channel = self._channel(
            provider_id=self.provider_id,
            bot_user_id=self.bot_user_id,
            credentials=True,
        )
        self.recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.channel,
            enabled=False,
            friendship_state="unknown",
        )
        self.other_channel = self._channel(provider_id=self.provider_id)
        self.other_channel_recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.other_channel,
            enabled=True,
            friendship_state="unknown",
        )
        other_identity = LineIdentity.objects.create(
            provider_id="0099999999",
            subject=self.subject,
            display_name="Other provider",
        )
        self.other_identity_recipient = DeliveryRecipient.objects.create(
            identity=other_identity,
            line_channel=self.channel,
            enabled=True,
            friendship_state="not_friend",
        )

    def _channel(
        self,
        *,
        provider_id: str,
        bot_user_id: str | None = None,
        credentials: bool = False,
    ) -> LineChannel:
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=bot_user_id or ("U" + uuid4().hex),
            label="統合試験チャネル",
            provider_id=provider_id,
            is_active=True,
        )
        if credentials:
            cipher = FernetCredentialCipher(runtime.get_validated_keyring())
            access_token = cipher.encrypt(
                AccessToken("integration-access-token"),
                CredentialContext(channel.public_id, "access_token"),
            )
            channel_secret = cipher.encrypt(
                ChannelSecret(self.secret),
                CredentialContext(channel.public_id, "channel_secret"),
            )
            LineChannelCredential.objects.create(
                line_channel=channel,
                access_token_ciphertext=access_token.ciphertext,
                channel_secret_ciphertext=channel_secret.ciphertext,
            )
        return channel

    def _post(
        self,
        *,
        event_type: str,
        event_id: str,
        occurred_at_ms: int,
        is_redelivery: bool = False,
        source: dict[str, object] | None = None,
        follow: dict[str, object] | None = None,
    ):
        event: dict[str, object] = {
            "webhookEventId": event_id,
            "type": event_type,
            "timestamp": occurred_at_ms,
            "deliveryContext": {"isRedelivery": is_redelivery},
            "source": source or {"type": "user", "userId": self.subject},
        }
        if follow is not None:
            event["follow"] = follow
        raw_body = json.dumps(
            {"destination": self.bot_user_id, "events": [event]},
            separators=(",", ":"),
        ).encode("utf-8")
        signature = base64.b64encode(
            hmac.new(self.secret.encode(), raw_body, hashlib.sha256).digest()
        ).decode("ascii")
        return self.client.post(
            f"/api/line/webhooks/{self.channel.public_id}/",
            data=raw_body,
            content_type="application/json",
            HTTP_X_LINE_SIGNATURE=signature,
        )

    def _after_registration(self, offset_ms: int = 1) -> int:
        return int(self.recipient.created_at.timestamp() * 1000) + offset_ms

    # テストケース: disabled recipientへ署名済みfollowを送信する
    # 期待値: exact matchするstate/orderだけが更新され、他関係と利用者設定は不変となる
    def test_signed_follow_updates_only_exact_recipient(self) -> None:
        occurred_at_ms = self._after_registration()
        target_non_owned = (
            self.recipient.identity_id,
            self.recipient.line_channel_id,
            self.recipient.created_at,
            self.recipient.enabled,
        )
        channel_active = self.channel.is_active
        other_channel_snapshot = (
            self.other_channel_recipient.friendship_state,
            self.other_channel_recipient.enabled,
            self.other_channel_recipient.last_friendship_event_occurred_at_ms,
            self.other_channel_recipient.last_friendship_webhook_event_id,
        )
        other_identity_snapshot = (
            self.other_identity_recipient.friendship_state,
            self.other_identity_recipient.enabled,
            self.other_identity_recipient.last_friendship_event_occurred_at_ms,
            self.other_identity_recipient.last_friendship_webhook_event_id,
        )
        response = self._post(
            event_type="follow",
            event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            occurred_at_ms=occurred_at_ms,
            follow={"isUnblocked": True},
        )

        self.assertEqual(response.status_code, 200)
        self.recipient.refresh_from_db()
        self.other_channel_recipient.refresh_from_db()
        self.other_identity_recipient.refresh_from_db()
        self.assertEqual(self.recipient.friendship_state, "friend")
        self.assertEqual(
            (
                self.recipient.identity_id,
                self.recipient.line_channel_id,
                self.recipient.created_at,
                self.recipient.enabled,
            ),
            target_non_owned,
        )
        self.assertEqual(
            self.recipient.last_friendship_event_occurred_at_ms,
            occurred_at_ms,
        )
        self.assertEqual(
            self.recipient.last_friendship_webhook_event_id,
            "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        )
        self.assertEqual(
            (
                self.other_channel_recipient.friendship_state,
                self.other_channel_recipient.enabled,
                self.other_channel_recipient.last_friendship_event_occurred_at_ms,
                self.other_channel_recipient.last_friendship_webhook_event_id,
            ),
            other_channel_snapshot,
        )
        self.assertEqual(
            (
                self.other_identity_recipient.friendship_state,
                self.other_identity_recipient.enabled,
                self.other_identity_recipient.last_friendship_event_occurred_at_ms,
                self.other_identity_recipient.last_friendship_webhook_event_id,
            ),
            other_identity_snapshot,
        )
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.is_active, channel_active)
        self.assertEqual(FriendshipSyncAudit.objects.get().outcome, "applied")
        self.assertEqual(WebhookEventReceipt.objects.get().status, "processed")
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: identity/recipient欠落、不正、対象外、stale、duplicate、provider未設定を処理する
    # 期待値: すべてHTTP成功とsafeな正常非更新監査へ収束し、recipientを作成しない
    def test_signed_normal_non_updates_are_safely_audited(self) -> None:
        baseline_ms = int(self.recipient.created_at.timestamp() * 1000)
        self.assertEqual(
            self._post(
                event_type="follow",
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FAY",
                occurred_at_ms=baseline_ms + 1,
                source={"type": "user"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self._post(
                event_type="follow",
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FAZ",
                occurred_at_ms=baseline_ms + 2,
                source={"type": "group", "groupId": "safe-group"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self._post(
                event_type="follow",
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FB0",
                occurred_at_ms=baseline_ms + 3,
                follow={"isUnblocked": 1},
            ).status_code,
            200,
        )
        self.assertEqual(
            self._post(
                event_type="unfollow",
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FB1",
                occurred_at_ms=baseline_ms + 4,
                source={"type": "room", "roomId": "safe-room"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self._post(
                event_type="follow",
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FB2",
                occurred_at_ms=baseline_ms,
            ).status_code,
            200,
        )
        duplicate_id = "01ARZ3NDEKTSV4RRFFQ69G5FB3"
        duplicate_time = baseline_ms + 5
        DeliveryRecipient.objects.filter(pk=self.recipient.pk).update(
            friendship_state="friend",
            last_friendship_event_occurred_at_ms=duplicate_time,
            last_friendship_webhook_event_id=duplicate_id,
        )
        self.assertEqual(
            self._post(
                event_type="follow",
                event_id=duplicate_id,
                occurred_at_ms=duplicate_time,
            ).status_code,
            200,
        )

        identity_count = LineIdentity.objects.count()
        recipient_count = DeliveryRecipient.objects.count()
        missing_subject = "U" + "b" * 32
        self.assertEqual(
            self._post(
                event_type="follow",
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FB4",
                occurred_at_ms=baseline_ms + 6,
                source={"type": "user", "userId": missing_subject},
            ).status_code,
            200,
        )
        self.assertEqual(LineIdentity.objects.count(), identity_count)
        self.assertEqual(DeliveryRecipient.objects.count(), recipient_count)
        self.assertFalse(LineIdentity.objects.filter(subject=missing_subject).exists())
        DeliveryRecipient.objects.filter(pk=self.recipient.pk).delete()
        self.assertEqual(
            self._post(
                event_type="unfollow",
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FB5",
                occurred_at_ms=baseline_ms + 7,
            ).status_code,
            200,
        )
        LineChannel.objects.filter(pk=self.channel.pk).update(provider_id=None)
        self.assertEqual(
            self._post(
                event_type="follow",
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FB6",
                occurred_at_ms=baseline_ms + 8,
            ).status_code,
            200,
        )

        self.assertEqual(
            list(
                FriendshipSyncAudit.objects.order_by("pk").values_list(
                    "outcome", flat=True
                )
            ),
            [
                "invalid",
                "out_of_scope",
                "invalid",
                "out_of_scope",
                "stale",
                "duplicate",
                "unlinked",
                "unlinked",
                "unresolvable",
            ],
        )
        self.assertEqual(
            set(WebhookEventReceipt.objects.values_list("status", flat=True)),
            {"processed"},
        )
        invalid_audit = FriendshipSyncAudit.objects.order_by("pk").first()
        assert invalid_audit is not None
        self.assertEqual(invalid_audit.channel_public_id, self.channel.public_id)
        self.assertEqual(
            invalid_audit.webhook_event_id,
            "01ARZ3NDEKTSV4RRFFQ69G5FAY",
        )
        self.assertEqual(invalid_audit.event_type, "follow")
        self.assertEqual(invalid_audit.occurred_at_ms, baseline_ms + 1)
        self.assertFalse(
            DeliveryRecipient.objects.filter(
                identity=self.identity,
                line_channel=self.channel,
            ).exists()
        )

    # テストケース: order metadataがnullの既存recipientへ境界前後のeventを送信する
    # 期待値: 登録境界以前をstaleとして拒否し、直後のeventからorder追跡を開始する
    def test_legacy_null_order_starts_tracking_after_registration_boundary(self) -> None:
        baseline_ms = int(self.recipient.created_at.timestamp() * 1000)
        DeliveryRecipient.objects.filter(pk=self.recipient.pk).update(
            friendship_state="friend"
        )
        self.recipient.refresh_from_db()
        self.assertIsNone(self.recipient.last_friendship_event_occurred_at_ms)
        self.assertIsNone(self.recipient.last_friendship_webhook_event_id)

        stale = self._post(
            event_type="follow",
            event_id="01ARZ3NDEKTSV4RRFFQ69G5FB7",
            occurred_at_ms=baseline_ms,
        )
        applied_time = baseline_ms + 1
        applied = self._post(
            event_type="follow",
            event_id="01ARZ3NDEKTSV4RRFFQ69G5FB8",
            occurred_at_ms=applied_time,
        )

        self.assertEqual(stale.status_code, 200)
        self.assertEqual(applied.status_code, 200)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.friendship_state, "friend")
        self.assertEqual(
            self.recipient.last_friendship_event_occurred_at_ms,
            applied_time,
        )
        self.assertEqual(
            self.recipient.last_friendship_webhook_event_id,
            "01ARZ3NDEKTSV4RRFFQ69G5FB8",
        )
        self.assertEqual(
            list(
                FriendshipSyncAudit.objects.order_by("pk").values_list(
                    "outcome", flat=True
                )
            ),
            ["stale", "state_maintained"],
        )

    # テストケース: state/order更新後の監査insertを故障注入する
    # 期待値: projection全体をrollbackし、HTTP受付は成功でもreceiptをhandler failureにする
    def test_audit_failure_rolls_back_projection_and_fails_receipt(self) -> None:
        occurred_at_ms = self._after_registration()
        with patch.object(
            DjangoFriendshipAuditRepository,
            "record",
            side_effect=RuntimeError("injected safe audit failure"),
        ):
            response = self._post(
                event_type="follow",
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FB9",
                occurred_at_ms=occurred_at_ms,
            )

        self.assertEqual(response.status_code, 200)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.friendship_state, "unknown")
        self.assertIsNone(self.recipient.last_friendship_event_occurred_at_ms)
        self.assertIsNone(self.recipient.last_friendship_webhook_event_id)
        self.assertEqual(FriendshipSyncAudit.objects.count(), 0)
        receipt = WebhookEventReceipt.objects.get()
        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.failure_code, "handler_failed")

    # テストケース: 署名済みunfollowと同状態の新しいunfollowを順に送信する
    # 期待値: not_friendへ収束後はstateを維持してorderだけ前進し、配信を開始しない
    def test_signed_unfollow_and_same_state_event_advance_order(self) -> None:
        first_occurred_at_ms = self._after_registration(1)
        second_occurred_at_ms = self._after_registration(2)
        first = self._post(
            event_type="unfollow",
            event_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
            occurred_at_ms=first_occurred_at_ms,
        )
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.friendship_state, "not_friend")
        self.assertEqual(
            self.recipient.last_friendship_event_occurred_at_ms,
            first_occurred_at_ms,
        )
        second = self._post(
            event_type="unfollow",
            event_id="01ARZ3NDEKTSV4RRFFQ69G5FAX",
            occurred_at_ms=second_occurred_at_ms,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.friendship_state, "not_friend")
        self.assertFalse(self.recipient.enabled)
        self.assertEqual(
            self.recipient.last_friendship_event_occurred_at_ms,
            second_occurred_at_ms,
        )
        self.assertEqual(
            self.recipient.last_friendship_webhook_event_id,
            "01ARZ3NDEKTSV4RRFFQ69G5FAX",
        )
        self.assertEqual(
            list(
                FriendshipSyncAudit.objects.order_by("pk").values_list(
                    "outcome", flat=True
                )
            ),
            ["applied", "state_maintained"],
        )
        self.assertEqual(DeliveryAttempt.objects.count(), 0)
