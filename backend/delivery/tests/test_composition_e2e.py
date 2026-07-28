import base64
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from delivery.models import DeliveryAttempt
from lineaccounts.authentication import (
    OWNER_SESSION_KEY,
    OwnerPrincipal,
    OwnerSessionContext,
)
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import DeliveryRecipient, LineIdentity
from lineaccounts.repositories import (
    DjangoAccountRepository,
    OwnerSessionView,
)
from lineaccounts.types import LineSubject
from linechannels import runtime
from linechannels.crypto import FernetCredentialCipher
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.types import AccessToken, ChannelSecret, CredentialContext
from lineinteractions.models import InteractionAudit
from linewebhooks.container import build_webhook_ingress_service
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.views import WebhookAPIView


_PROVIDER_ID = "0012345678"
_ACCESS_TOKEN_CANARY = "composition-access-token-canary"
_CHANNEL_SECRET_CANARY = "composition-channel-secret-canary"
_LINE_SUBJECT_CANARY = "U" + "c" * 32
_BOT_USER_ID_CANARY = "U" + "b" * 32
_REPLY_TOKEN_CANARY = "composition-reply-token-canary"
_MESSAGING_CHANNEL_ID_CANARY = "composition-channel-id-canary"
_DISPLAY_NAME_PII_CANARY = "composition-display-name-pii-canary"
_EVENT_ID = "01ARZ3NDEKTSV4RRFFQ69G9000"


class _SafePushAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def push_message_with_http_info(self, **kwargs: object):
        self.calls.append(kwargs)
        return None, 200, {"X-Line-Request-Id": "line-request-safe"}


class _NoReplyGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def reply_text(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        raise AssertionError("receipt action must not start a LINE reply")


class LinkedDeliveryCompositionE2ETests(APITestCase):
    def setUp(self) -> None:
        runtime.load_credential_keyring()
        repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = repository.lock_owner_account()
            identity_summary = repository.upsert_identity(
                VerifiedLineIdentity(
                    _PROVIDER_ID,
                    LineSubject(_LINE_SUBJECT_CANARY),
                    _DISPLAY_NAME_PII_CANARY,
                )
            )
            owner = repository.bind_owner_identity(
                owner,
                identity_summary.public_id,
            )
            self.owner_session = repository.create_owner_session(
                owner,
                timezone.now() + timedelta(hours=8),
            )
        self.identity = LineIdentity.objects.get(
            public_id=identity_summary.public_id
        )
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id=_MESSAGING_CHANNEL_ID_CANARY,
            bot_user_id=_BOT_USER_ID_CANARY,
            label="Composition channel",
            provider_id=_PROVIDER_ID,
            is_active=True,
        )
        cipher = FernetCredentialCipher(runtime.get_validated_keyring())
        access_token = cipher.encrypt(
            AccessToken(_ACCESS_TOKEN_CANARY),
            CredentialContext(self.channel.public_id, "access_token"),
        )
        channel_secret = cipher.encrypt(
            ChannelSecret(_CHANNEL_SECRET_CANARY),
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
            enabled=True,
            friendship_state=DeliveryRecipient.FriendshipState.FRIEND,
        )

        client = APIClient(enforce_csrf_checks=True)
        session = client.session
        session[OWNER_SESSION_KEY] = str(self.owner_session.public_id)
        session.save()
        bootstrap = client.get("/api/account/session/")
        client.credentials(
            HTTP_ORIGIN="https://test.example.ngrok.app",
            HTTP_X_CSRFTOKEN=bootstrap.cookies["csrftoken"].value,
        )
        self.client = client

    def _other_owner_client(self) -> APIClient:
        session_id = uuid4()
        identity_id = uuid4()
        session = OwnerSessionView(
            public_id=session_id,
            owner_slot=2,
            identity_id=identity_id,
            display_name="Other owner",
            owner_state="active",
            expires_at=timezone.now() + timedelta(hours=8),
        )
        client = APIClient(enforce_csrf_checks=True)
        bootstrap = client.get("/api/account/session/")
        client.credentials(
            HTTP_ORIGIN="https://test.example.ngrok.app",
            HTTP_X_CSRFTOKEN=bootstrap.cookies["csrftoken"].value,
        )
        client.force_authenticate(
            user=OwnerPrincipal(
                owner_session_id=session_id,
                identity_public_id=identity_id,
                account_state="active",
            ),
            token=OwnerSessionContext(session=session),
        )
        return client

    @staticmethod
    def _signed_postback(capability: str) -> tuple[bytes, str]:
        payload = {
            "destination": _BOT_USER_ID_CANARY,
            "events": [
                {
                    "webhookEventId": _EVENT_ID,
                    "type": "postback",
                    "timestamp": int(timezone.now().timestamp() * 1000),
                    "deliveryContext": {"isRedelivery": False},
                    "source": {
                        "type": "user",
                        "userId": _LINE_SUBJECT_CANARY,
                    },
                    "replyToken": _REPLY_TOKEN_CANARY,
                    "postback": {
                        "data": f"v1:delivery.received:{capability}",
                    },
                }
            ],
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = base64.b64encode(
            hmac.new(
                _CHANNEL_SECRET_CANARY.encode(),
                raw,
                hashlib.sha256,
            ).digest()
        ).decode()
        return raw, signature

    # テストケース: receipt付き配信をpreviewからpush、status、署名済みpostback、再statusまでproduction compositionで通す
    # 期待値: 一つのoperationを一回だけpushし、LINE受付とreceipt確認を独立確定してreplyや秘密露出を起こさない
    def test_receipt_delivery_completes_through_production_composition(
        self,
    ) -> None:
        operation_id = uuid4()
        push_api = _SafePushAPI()
        log_patcher = patch("logging.Logger._log")
        log_call = log_patcher.start()
        self.addCleanup(log_patcher.stop)

        preview = self.client.post(
            "/api/deliveries/preview/",
            {
                "channelId": str(self.channel.public_id),
                "recipientId": str(self.recipient.public_id),
                "subject": "構成テスト",
                "body": "本文",
                "receiptRequested": True,
            },
            format="json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(
            preview.data["recipientDisplayName"],
            _DISPLAY_NAME_PII_CANARY,
        )

        send_payload = {
            "channelId": str(self.channel.public_id),
            "recipientId": str(self.recipient.public_id),
            "subject": "構成テスト",
            "body": "本文",
            "receiptRequested": True,
            "operationId": str(operation_id),
            "confirmationToken": preview.data["confirmationToken"],
        }
        with patch(
            "delivery.gateway.LINEChannelPushGateway._build_api",
            return_value=push_api,
        ) as api_factory:
            sent = self.client.post(
                "/api/deliveries/",
                send_payload,
                format="json",
            )
            existing = self.client.post(
                "/api/deliveries/",
                send_payload,
                format="json",
            )

        self.assertEqual(sent.status_code, 201)
        self.assertEqual(existing.status_code, 200)
        self.assertEqual(existing.data, sent.data)
        self.assertEqual(sent.data["operationId"], str(operation_id))
        self.assertEqual(sent.data["status"], "succeeded")
        self.assertEqual(sent.data["lineRequestId"], "line-request-safe")
        self.assertEqual(sent.data["receipt"]["status"], "pending")
        self.assertEqual(DeliveryAttempt.objects.count(), 1)
        attempt = DeliveryAttempt.objects.get(operation_id=operation_id)
        self.assertEqual(attempt.status, DeliveryAttempt.Status.SUCCEEDED)
        self.assertIsNone(attempt.receipt_confirmed_at)
        self.assertIsNone(attempt.receipt_webhook_event_id)

        api_factory.assert_called_once_with(_ACCESS_TOKEN_CANARY)
        self.assertEqual(len(push_api.calls), 1)
        push_kwargs = push_api.calls[0]
        self.assertEqual(
            push_kwargs["x_line_retry_key"],
            str(operation_id),
        )
        push_request = push_kwargs["push_message_request"]
        self.assertEqual(push_request.to, _LINE_SUBJECT_CANARY)
        self.assertEqual(len(push_request.messages), 2)
        action_data = push_request.messages[1].template.actions[0].data
        action_prefix = "v1:delivery.received:"
        self.assertTrue(action_data.startswith(action_prefix))
        capability = action_data.removeprefix(action_prefix)
        self.assertNotEqual(capability, "")

        pending_status = self.client.post(
            f"/api/deliveries/{operation_id}/status/",
            format="json",
        )
        self.assertEqual(pending_status.status_code, 200)
        self.assertEqual(pending_status.data["status"], "succeeded")
        self.assertEqual(pending_status.data["receipt"]["status"], "pending")

        other_owner_status = self._other_owner_client().post(
            f"/api/deliveries/{operation_id}/status/",
            format="json",
        )
        self.assertEqual(other_owner_status.status_code, 404)
        self.assertEqual(
            other_owner_status.data,
            {
                "error": {
                    "code": "operation_not_found",
                    "summary": "送信操作を確認できませんでした。",
                }
            },
        )

        reply_gateway = _NoReplyGateway()
        with patch(
            "lineinteractions.container.HttpxLineReplyGateway",
            return_value=reply_gateway,
        ):
            webhook_service = build_webhook_ingress_service()
        raw, signature = self._signed_postback(capability)
        with (
            patch.object(
                WebhookAPIView,
                "service_factory",
                return_value=webhook_service,
            ),
        ):
            received = self.client.post(
                f"/api/line/webhooks/{self.channel.public_id}/",
                data=raw,
                content_type="application/json",
                HTTP_X_LINE_SIGNATURE=signature,
            )
            attempt.refresh_from_db()
            first_confirmation = (
                attempt.receipt_confirmed_at,
                attempt.receipt_webhook_event_id,
            )
            redelivered = self.client.post(
                f"/api/line/webhooks/{self.channel.public_id}/",
                data=raw,
                content_type="application/json",
                HTTP_X_LINE_SIGNATURE=signature,
            )

        self.assertEqual(received.status_code, 200)
        self.assertEqual(redelivered.status_code, 200)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, DeliveryAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.line_request_id, "line-request-safe")
        self.assertIsNotNone(attempt.receipt_confirmed_at)
        self.assertEqual(attempt.receipt_webhook_event_id, _EVENT_ID)
        self.assertEqual(
            (
                attempt.receipt_confirmed_at,
                attempt.receipt_webhook_event_id,
            ),
            first_confirmation,
        )
        self.assertEqual(reply_gateway.calls, [])
        self.assertEqual(
            InteractionAudit.objects.get(
                webhook_event_id=_EVENT_ID
            ).interaction_outcome,
            "action_succeeded",
        )
        self.assertEqual(
            WebhookEventReceipt.objects.get(
                webhook_event_id=_EVENT_ID
            ).status,
            "processed",
        )
        self.assertEqual(
            InteractionAudit.objects.filter(
                webhook_event_id=_EVENT_ID
            ).count(),
            1,
        )
        self.assertEqual(
            WebhookEventReceipt.objects.filter(
                webhook_event_id=_EVENT_ID
            ).count(),
            1,
        )

        confirmed_status = self.client.post(
            f"/api/deliveries/{operation_id}/status/",
            format="json",
        )
        self.assertEqual(confirmed_status.status_code, 200)
        self.assertEqual(confirmed_status.data["status"], "succeeded")
        self.assertEqual(
            confirmed_status.data["receipt"]["status"],
            "confirmed",
        )
        self.assertIsNotNone(
            confirmed_status.data["receipt"]["confirmedAt"]
        )
        self.assertEqual(DeliveryAttempt.objects.count(), 1)

        persisted_surfaces = (
            list(DeliveryAttempt.objects.values()),
            list(InteractionAudit.objects.values()),
            list(WebhookEventReceipt.objects.values()),
            repr(attempt),
            repr(InteractionAudit.objects.get(webhook_event_id=_EVENT_ID)),
            repr(WebhookEventReceipt.objects.get(webhook_event_id=_EVENT_ID)),
        )
        public_and_audit_surfaces = repr(
            (
                sent.data,
                existing.data,
                pending_status.data,
                other_owner_status.data,
                received.content,
                redelivered.content,
                confirmed_status.data,
                persisted_surfaces,
                log_call.call_args_list,
            )
        )
        for forbidden in (
            _ACCESS_TOKEN_CANARY,
            _CHANNEL_SECRET_CANARY,
            _LINE_SUBJECT_CANARY,
            _BOT_USER_ID_CANARY,
            _REPLY_TOKEN_CANARY,
            _MESSAGING_CHANNEL_ID_CANARY,
            _DISPLAY_NAME_PII_CANARY,
            capability,
            preview.data["confirmationToken"],
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, public_and_audit_surfaces)

    # テストケース: status取得の下位DB例外が表示名・LINE subject・secret canaryを含む
    # 期待値: owner向け応答と通常logをsafe分類へ縮約し、生例外の秘密／PIIを観測面へ出さない
    def test_status_storage_exception_is_safely_collapsed_without_logging_canaries(
        self,
    ) -> None:
        operation_id = uuid4()
        poisoned_exception = DatabaseError(
            ":".join(
                (
                    _DISPLAY_NAME_PII_CANARY,
                    _LINE_SUBJECT_CANARY,
                    _ACCESS_TOKEN_CANARY,
                    _CHANNEL_SECRET_CANARY,
                    "receipt-capability-exception-canary",
                )
            )
        )

        with (
            patch(
                "delivery.views.DeliveryService.check_linked_status",
                side_effect=poisoned_exception,
            ),
            patch("logging.Logger._log") as log_call,
        ):
            response = self.client.post(
                f"/api/deliveries/{operation_id}/status/",
                format="json",
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.data,
            {
                "error": {
                    "code": "storage_unavailable",
                    "summary": "処理を完了できませんでした。",
                }
            },
        )
        observed = repr((response.data, response.content, log_call.call_args_list))
        for forbidden in (
            _DISPLAY_NAME_PII_CANARY,
            _LINE_SUBJECT_CANARY,
            _ACCESS_TOKEN_CANARY,
            _CHANNEL_SECRET_CANARY,
            "receipt-capability-exception-canary",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, observed)

    # テストケース: 選択channelのcredentialが消失した状態でfixed環境値を設定し、同じlinked送信を再要求する
    # 期待値: fixed fallbackも自動再送も行わず、単一attemptのconfiguration失敗へ安全に収束する
    @override_settings(
        LINE_CHANNEL_ACCESS_TOKEN="fixed-fallback-token-canary",
        LINE_USER_ID="fixed-fallback-subject-canary",
    )
    def test_missing_selected_credential_never_uses_fixed_fallback_or_resends(
        self,
    ) -> None:
        operation_id = uuid4()
        preview = self.client.post(
            "/api/deliveries/preview/",
            {
                "channelId": str(self.channel.public_id),
                "recipientId": str(self.recipient.public_id),
                "subject": "fallback禁止",
                "body": "本文",
                "receiptRequested": False,
            },
            format="json",
        )
        self.assertEqual(preview.status_code, 200)
        LineChannelCredential.objects.filter(line_channel=self.channel).delete()
        payload = {
            "channelId": str(self.channel.public_id),
            "recipientId": str(self.recipient.public_id),
            "subject": "fallback禁止",
            "body": "本文",
            "receiptRequested": False,
            "operationId": str(operation_id),
            "confirmationToken": preview.data["confirmationToken"],
        }

        with (
            patch(
                "delivery.gateway.LINEChannelPushGateway._build_api",
                side_effect=AssertionError("linked gateway must not start"),
            ) as linked_api_factory,
        ):
            first = self.client.post("/api/deliveries/", payload, format="json")
            repeated = self.client.post("/api/deliveries/", payload, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.data, first.data)
        self.assertEqual(first.data["status"], "failed")
        self.assertEqual(first.data["error"]["code"], "configuration")
        self.assertEqual(DeliveryAttempt.objects.count(), 1)
        linked_api_factory.assert_not_called()
        observed = repr(
            (
                first.data,
                repeated.data,
                list(DeliveryAttempt.objects.values()),
            )
        )
        self.assertNotIn("fixed-fallback-token-canary", observed)
        self.assertNotIn("fixed-fallback-subject-canary", observed)
