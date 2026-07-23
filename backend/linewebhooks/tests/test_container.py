import base64
import hashlib
import hmac
import json
from time import monotonic

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from linechannels import runtime
from linechannels.crypto import FernetCredentialCipher
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.repositories import DjangoWebhookCredentialRepository
from linechannels.types import AccessToken, ChannelSecret, CredentialContext
from linefriendships.services import DefaultFriendshipSyncService
from linewebhooks.audit import SafeWebhookAuditLogger
from linewebhooks.container import build_webhook_ingress_service
from linewebhooks.container import get_webhook_ingress_service
from linewebhooks.handlers import StaticHandlerRegistry
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.repositories import DjangoEventReceiptRepository
from linewebhooks.services import WebhookIngressService
from linewebhooks.verification import RawSignatureVerifier, WebhookPayloadValidator


class WebhookCompositionRootTests(SimpleTestCase):
    # テストケース: startupで構築済みのWebhook serviceを二度取得する
    # 期待値: requestごとに再構築せずprocess内の同一instanceを返す
    def test_returns_one_startup_cached_service_instance(self) -> None:
        self.assertIs(
            get_webhook_ingress_service(),
            get_webhook_ingress_service(),
        )

    # テストケース: Webhook ingress serviceをcomposition rootから構築する
    # 期待値: follow/unfollowだけに同一の友だち同期handlerが登録される
    def test_builds_service_with_friendship_handler_for_follow_and_unfollow(self) -> None:
        runtime.load_credential_keyring()
        service = build_webhook_ingress_service()

        self.assertIsInstance(service, WebhookIngressService)
        self.assertIsInstance(
            service._credential_repository,
            DjangoWebhookCredentialRepository,
        )
        self.assertIsInstance(service._signature_verifier, RawSignatureVerifier)
        self.assertIsInstance(service._payload_validator, WebhookPayloadValidator)
        self.assertIsInstance(
            service._receipt_repository,
            DjangoEventReceiptRepository,
        )
        self.assertIsInstance(service._registry, StaticHandlerRegistry)
        follow_registration = service._registry.resolve("follow")
        unfollow_registration = service._registry.resolve("unfollow")
        assert follow_registration is not None
        assert unfollow_registration is not None
        follow_handler = follow_registration.handler
        unfollow_handler = unfollow_registration.handler
        self.assertIsInstance(follow_handler, DefaultFriendshipSyncService)
        self.assertIs(follow_handler, unfollow_handler)
        self.assertEqual(follow_registration.execution_profile, "local")
        self.assertEqual(unfollow_registration.execution_profile, "local")
        self.assertIsNone(service._registry.resolve("message"))
        self.assertIsInstance(service._audit_logger, SafeWebhookAuditLogger)
        self.assertIs(service._monotonic_clock, monotonic)
        self.assertIs(service._observed_at_clock, timezone.now)


class PublicWebhookRouteTests(TestCase):
    def setUp(self) -> None:
        runtime.load_credential_keyring()
        self.secret = "route-channel-secret"
        self.bot_user_id = "U" + "1" * 32
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id="1234567890",
            bot_user_id=self.bot_user_id,
            label="Webhook route",
            is_active=True,
        )
        cipher = FernetCredentialCipher(runtime.get_validated_keyring())
        access_token = cipher.encrypt(
            AccessToken("access-token"),
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
        self.url = f"/api/line/webhooks/{self.channel.public_id}/"

    def _post(self, payload: dict[str, object]):
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = base64.b64encode(
            hmac.new(self.secret.encode(), raw_body, hashlib.sha256).digest()
        ).decode("ascii")
        return self.client.post(
            self.url,
            data=raw_body,
            content_type="application/json",
            HTTP_X_LINE_SIGNATURE=signature,
        )

    # テストケース: 公開routeへ署名済みの空eventsをPOSTする
    # 期待値: concrete compositionを通って空200となりreceiptを作成しない
    def test_signed_empty_request_reaches_composed_service(self) -> None:
        response = self._post({"destination": self.bot_user_id, "events": []})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(WebhookEventReceipt.objects.count(), 0)

    # テストケース: 空registryの公開routeへ有効な未知eventをPOSTする
    # 期待値: unsupportedとして安全に受付し、空200とterminal receiptを返す
    def test_valid_event_is_accepted_as_unsupported_by_default(self) -> None:
        response = self._post(
            {
                "destination": self.bot_user_id,
                "events": [
                    {
                        "webhookEventId": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                        "type": "future-event",
                        "timestamp": 100,
                        "deliveryContext": {"isRedelivery": False},
                    }
                ],
            }
        )

        self.assertEqual(response.status_code, 200)
        receipt = WebhookEventReceipt.objects.get()
        self.assertEqual(receipt.status, "unsupported")
        self.assertEqual(receipt.channel_public_id, self.channel.public_id)
