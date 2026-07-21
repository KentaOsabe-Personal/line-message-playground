import base64
import hashlib
import hmac
import json
import logging
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
from linefriendships.parsing import DefaultFriendshipEventParser
from linefriendships.repositories import DjangoFriendshipAuditRepository
from linefriendships.services import DefaultFriendshipSyncService
from linewebhooks.audit import SafeWebhookAuditLogger
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.types import FrozenJsonObject, HandlerFailed, VerifiedWebhookEvent


VALID_USER_CANARY = "U" + "c" * 32
INVALID_USER_CANARY = "invalid-line-user-canary"
EXCEPTION_CANARY = "friendship-exception-canary"
SAFE_AUDIT_FIELDS = {
    "id",
    "channel_public_id",
    "webhook_event_id",
    "event_type",
    "occurred_at_ms",
    "outcome",
    "is_unblocked",
    "recorded_at",
}


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _FailingAccountRepository:
    def lock_target(self, **kwargs: object) -> object:
        raise RuntimeError(EXCEPTION_CANARY)

    def apply_locked(self, target: object, **kwargs: object) -> None:
        raise AssertionError("unreachable")


class FriendshipSecurityIntegrationTests(TestCase):
    def setUp(self) -> None:
        runtime.load_credential_keyring()
        self.provider_id = "0012345678"
        self.secret = "friendship-security-secret"
        self.bot_user_id = "U" + "1" * 32
        self.identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=VALID_USER_CANARY,
            display_name="Owner",
        )
        owner, _ = OwnerAccount.objects.get_or_create(slot=1)
        OwnerAccount.objects.filter(pk=owner.pk).update(
            state=OwnerAccount.State.ACTIVE,
            identity=self.identity,
        )
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=self.bot_user_id,
            label="セキュリティ試験チャネル",
            provider_id=self.provider_id,
            is_active=True,
        )
        cipher = FernetCredentialCipher(runtime.get_validated_keyring())
        access_token = cipher.encrypt(
            AccessToken("security-access-token"),
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
        self.log_handler = _ListHandler()
        self.logger = logging.getLogger("linewebhooks.audit")
        self.original_handlers = self.logger.handlers[:]
        self.original_level = self.logger.level
        self.original_propagate = self.logger.propagate
        self.logger.handlers = [self.log_handler]
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    def tearDown(self) -> None:
        self.logger.handlers = self.original_handlers
        self.logger.setLevel(self.original_level)
        self.logger.propagate = self.original_propagate

    def _post(self, events: list[dict[str, object]]):
        raw_body = json.dumps(
            {"destination": self.bot_user_id, "events": events},
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

    def _event(
        self,
        *,
        event_id: str,
        user_id: str,
        occurred_at_ms: int,
    ) -> dict[str, object]:
        return {
            "webhookEventId": event_id,
            "type": "follow",
            "timestamp": occurred_at_ms,
            "deliveryContext": {"isRedelivery": False},
            "source": {"type": "user", "userId": user_id},
        }

    # テストケース: valid/invalidなLINE user ID canaryを署名済みfollowとして処理する
    # 期待値: 同期監査・repr・通常log・公開responseへcanaryが漏れず、外部送信clientも起動しない
    def test_user_ids_and_external_effects_do_not_escape_public_surfaces(self) -> None:
        baseline_ms = int(self.recipient.created_at.timestamp() * 1000)
        events = [
            self._event(
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FA1",
                user_id=VALID_USER_CANARY,
                occurred_at_ms=baseline_ms + 1,
            ),
            self._event(
                event_id="01ARZ3NDEKTSV4RRFFQ69G5FA2",
                user_id=INVALID_USER_CANARY,
                occurred_at_ms=baseline_ms + 2,
            ),
        ]

        with (
            patch("delivery.views.DeliveryService") as delivery_service,
            patch("delivery.views.LINEGateway") as view_gateway,
            patch("delivery.services.LINEGateway") as service_gateway,
            patch("delivery.gateway.LINEGateway") as gateway_type,
            patch("delivery.gateway.ApiClient") as api_client,
            patch("delivery.gateway.MessagingApi") as messaging_api,
        ):
            response = self._post(events)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(DeliveryAttempt.objects.count(), 0)
        delivery_service.assert_not_called()
        view_gateway.assert_not_called()
        service_gateway.assert_not_called()
        gateway_type.assert_not_called()
        api_client.assert_not_called()
        messaging_api.assert_not_called()
        audits = list(FriendshipSyncAudit.objects.order_by("pk"))
        self.assertEqual([audit.outcome for audit in audits], ["applied", "invalid"])
        audit_fields = {field.name for field in FriendshipSyncAudit._meta.get_fields()}
        self.assertEqual(audit_fields, SAFE_AUDIT_FIELDS)
        audit_surface = list(
            FriendshipSyncAudit.objects.order_by("pk").values()
        )
        receipt_surface = list(WebhookEventReceipt.objects.order_by("pk").values())
        log_surface = [record.__dict__ for record in self.log_handler.records]
        rendered = "\n".join(
            str(surface)
            for surface in (
                response.content,
                audits,
                audit_surface,
                receipt_surface,
                log_surface,
            )
        )
        self.assertNotIn(VALID_USER_CANARY, rendered)
        self.assertNotIn(INVALID_USER_CANARY, rendered)

    # テストケース: user IDを含むeventのreprとcanary付きrepository例外を同期handlerへ渡す
    # 期待値: typed eventと安全な失敗結果のrepr・strへuser IDや生例外detailが現れない
    def test_repr_and_failure_result_redact_user_and_exception_canaries(self) -> None:
        occurred_at_ms = int(self.recipient.created_at.timestamp() * 1000) + 1
        event = VerifiedWebhookEvent(
            channel_public_id=self.channel.public_id,
            webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FA3",
            event_type="follow",
            occurred_at_ms=occurred_at_ms,
            is_redelivery=False,
            data=FrozenJsonObject(
                {
                    "type": "follow",
                    "source": {"type": "user", "userId": VALID_USER_CANARY},
                }
            ),
        )
        parser = DefaultFriendshipEventParser()
        parsed = parser.parse(event)
        service = DefaultFriendshipSyncService(
            parser=parser,
            channel_directory=type(
                "Directory",
                (),
                {"get": lambda _self, _public_id: type(
                    "Channel", (), {"provider_id": self.provider_id}
                )()},
            )(),
            account_repository=_FailingAccountRepository(),
            audit_repository=DjangoFriendshipAuditRepository(),
        )

        result = service.handle(event)

        self.assertIsInstance(result, HandlerFailed)
        rendered = "\n".join((repr(event), repr(parsed), repr(result), str(result)))
        self.assertNotIn(VALID_USER_CANARY, rendered)
        self.assertNotIn(EXCEPTION_CANARY, rendered)
        self.assertEqual(FriendshipSyncAudit.objects.count(), 0)
