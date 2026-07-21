import base64
import hashlib
import hmac
import json
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerAccount
from linechannels import runtime
from linechannels.crypto import FernetCredentialCipher
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.types import AccessToken, ChannelSecret, CredentialContext
from linefriendships.container import build_friendship_sync_handler
from linefriendships.models import FriendshipSyncAudit
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.types import FrozenJsonObject, HandlerSucceeded, VerifiedWebhookEvent


SINGLE_EVENT_RUNS = 5
TEN_EVENT_REQUEST_BUDGET_MS = 2_000
SINGLE_EVENT_QUERY_BUDGETS = {
    "valid": 8,
    "invalid": 3,
    "unlinked": 5,
    "stale": 6,
}


class FriendshipPerformanceIntegrationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        runtime.load_credential_keyring()
        self.provider_id = "0012345678"
        self.subject = "U" + "a" * 32
        self.secret = "friendship-performance-secret"
        self.identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=self.subject,
            display_name="Owner",
        )
        owner, _ = OwnerAccount.objects.get_or_create(slot=1)
        OwnerAccount.objects.filter(pk=owner.pk).update(
            state=OwnerAccount.State.ACTIVE,
            identity=self.identity,
        )
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id="U" + uuid4().hex,
            label="性能試験チャネル",
            provider_id=self.provider_id,
            is_active=True,
        )
        cipher = FernetCredentialCipher(runtime.get_validated_keyring())
        access_token = cipher.encrypt(
            AccessToken("performance-access-token"),
            CredentialContext(self.channel.public_id, "access_token"),
        )
        channel_secret = cipher.encrypt(
            ChannelSecret(self.secret),
            CredentialContext(self.channel.public_id, "channel_secret"),
        )
        LineChannelCredential.objects.create(
            line_channel=self.channel,
            access_token_ciphertext=access_token.ciphertext,
            channel_secret_ciphertext=channel_secret.ciphertext,
        )
        self.recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.channel,
            friendship_state="unknown",
        )
        self.baseline_ms = int(self.recipient.created_at.timestamp() * 1000)

    def _post(self, events: list[dict[str, object]]):
        raw_body = json.dumps(
            {"destination": self.channel.bot_user_id, "events": events},
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

    def _event(self, path: str, iteration: int) -> VerifiedWebhookEvent:
        event_type = "follow"
        subject = self.subject
        occurred_at_ms = self.baseline_ms + 100 + iteration
        if path == "valid":
            event_type = "follow" if iteration % 2 == 0 else "unfollow"
        elif path == "invalid":
            subject = "invalid-line-user-id"
        elif path == "unlinked":
            subject = "U" + "b" * 32
        elif path == "stale":
            occurred_at_ms = self.baseline_ms
        else:
            raise AssertionError(f"unknown path: {path}")
        return VerifiedWebhookEvent(
            channel_public_id=self.channel.public_id,
            webhook_event_id=f"{iteration + 1:026d}",
            event_type=event_type,
            occurred_at_ms=occurred_at_ms,
            is_redelivery=False,
            data=FrozenJsonObject(
                {
                    "type": event_type,
                    "source": {"type": "user", "userId": subject},
                }
            ),
        )

    # テストケース: 非事前lock競合のvalid・invalid・unlinked・staleを各5回処理する
    # 期待値: 各1eventが100ms未満かつpath別の固定query上限内で、外部I/Oを起動せず成功する
    def test_single_event_paths_meet_latency_and_query_budgets(self) -> None:
        self.assertFalse(connection.in_atomic_block)
        handler = build_friendship_sync_handler()

        with (
            patch("delivery.views.DeliveryService") as delivery_service,
            patch("delivery.views.LINEGateway") as view_gateway,
            patch("delivery.services.LINEGateway") as service_gateway,
            patch("delivery.gateway.ApiClient") as api_client,
            patch("delivery.gateway.MessagingApi") as messaging_api,
        ):
            for path, query_budget in SINGLE_EVENT_QUERY_BUDGETS.items():
                for iteration in range(SINGLE_EVENT_RUNS):
                    with self.subTest(path=path, iteration=iteration):
                        event = self._event(path, iteration)
                        started_at = perf_counter()
                        with CaptureQueriesContext(connection) as queries:
                            result = handler.handle(event)
                        elapsed_ms = (perf_counter() - started_at) * 1000

                        self.assertIsInstance(result, HandlerSucceeded)
                        self.assertLess(elapsed_ms, 100)
                        self.assertLessEqual(
                            len(queries),
                            query_budget,
                            msg=f"{path} query count: {len(queries)}",
                        )

        delivery_service.assert_not_called()
        view_gateway.assert_not_called()
        service_gateway.assert_not_called()
        api_client.assert_not_called()
        messaging_api.assert_not_called()

    # テストケース: 同時刻でID順を逆転した最大10件の署名済みfriendship eventを処理する
    # 期待値: requestが2,000ms未満で完了し、到着順によらず最大order keyの単一状態へ収束する
    def test_ten_signed_events_meet_deadline_and_converge_to_max_order_key(
        self,
    ) -> None:
        occurred_at_ms = self.baseline_ms + 100
        ordered = [
            (
                f"{sequence:026d}",
                "follow" if sequence % 2 else "unfollow",
            )
            for sequence in range(1, 11)
        ]
        events = [
            {
                "webhookEventId": event_id,
                "type": event_type,
                "timestamp": occurred_at_ms,
                "deliveryContext": {"isRedelivery": sequence % 2 == 0},
                "source": {"type": "user", "userId": self.subject},
            }
            for sequence, (event_id, event_type) in reversed(
                list(enumerate(ordered, start=1))
            )
        ]

        started_at = perf_counter()
        response = self._post(events)
        elapsed_ms = (perf_counter() - started_at) * 1000

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertLess(elapsed_ms, TEN_EVENT_REQUEST_BUDGET_MS)
        self.recipient.refresh_from_db()
        max_event_id, _ = max(ordered)
        self.assertEqual(self.recipient.friendship_state, "not_friend")
        self.assertEqual(
            self.recipient.last_friendship_event_occurred_at_ms,
            occurred_at_ms,
        )
        self.assertEqual(
            self.recipient.last_friendship_webhook_event_id,
            max_event_id,
        )
        self.assertEqual(WebhookEventReceipt.objects.count(), 10)
        self.assertEqual(FriendshipSyncAudit.objects.count(), 10)
