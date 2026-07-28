import base64
import hashlib
import hmac
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

from django.db import connections
from django.test import TransactionTestCase

from delivery.models import DeliveryAttempt
from delivery.receipt import ReceiptHandler
from delivery.repositories import DjangoAttemptRepository
from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerAccount
from linechannels import runtime
from linechannels.crypto import FernetCredentialCipher
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialContext,
)
from lineinteractions.models import InteractionAudit
from linewebhooks.audit import SafeWebhookAuditLogger
from linewebhooks.container import build_webhook_ingress_service
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.views import WebhookAPIView


_PROVIDER_ID = "0012345678"
_BOT_USER_ID = "U" + "1" * 32
_LINE_SUBJECT_CANARY = "U" + "a" * 32
_CHANNEL_SECRET_CANARY = "receipt-channel-secret-canary"
_ACCESS_TOKEN_CANARY = "receipt-access-token-canary"
_REPLY_TOKEN_CANARY = "receipt-reply-token-canary"
_DISPLAY_NAME_CANARY = "receipt-display-name-pii-canary"
_NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


class _NoReplyGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def reply_text(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        raise AssertionError("receipt action must not start a LINE reply")


class _CapturingLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _ReceiptHandlerSpy:
    def __init__(self, delegate: ReceiptHandler) -> None:
        self.delegate = delegate
        self.commands: list[object] = []
        self._lock = threading.Lock()

    def handle(self, command: object) -> object:
        with self._lock:
            self.commands.append(command)
        return self.delegate.handle(command)


class ReceiptPostbackIntegrationTests(TransactionTestCase):
    reset_sequences = True

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        OwnerAccount.objects.get_or_create(
            slot=1,
            defaults={"state": OwnerAccount.State.VACANT},
        )

    def setUp(self) -> None:
        runtime.load_credential_keyring()
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id="1234567890",
            bot_user_id=_BOT_USER_ID,
            label="Receipt integration",
            provider_id=_PROVIDER_ID,
            is_active=True,
        )
        cipher = FernetCredentialCipher(runtime.get_validated_keyring())
        encrypted_access_token = cipher.encrypt(
            AccessToken(_ACCESS_TOKEN_CANARY),
            CredentialContext(self.channel.public_id, "access_token"),
        )
        encrypted_channel_secret = cipher.encrypt(
            ChannelSecret(_CHANNEL_SECRET_CANARY),
            CredentialContext(self.channel.public_id, "channel_secret"),
        )
        LineChannelCredential.objects.create(
            line_channel=self.channel,
            access_token_ciphertext=encrypted_access_token.ciphertext,
            channel_secret_ciphertext=encrypted_channel_secret.ciphertext,
        )
        self.identity = LineIdentity.objects.create(
            provider_id=_PROVIDER_ID,
            subject=_LINE_SUBJECT_CANARY,
            display_name=_DISPLAY_NAME_CANARY,
        )
        OwnerAccount.objects.update_or_create(
            slot=1,
            defaults={
                "state": OwnerAccount.State.ACTIVE,
                "identity": self.identity,
            },
        )
        self.recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.channel,
            friendship_state=DeliveryRecipient.FriendshipState.FRIEND,
        )

    def _build_service(self):
        gateway = _NoReplyGateway()
        spies: list[_ReceiptHandlerSpy] = []

        def build_spy(**kwargs: object) -> _ReceiptHandlerSpy:
            spy = _ReceiptHandlerSpy(ReceiptHandler(**kwargs))
            spies.append(spy)
            return spy

        with (
            patch(
                "lineinteractions.container.HttpxLineReplyGateway",
                return_value=gateway,
            ),
            patch(
                "linewebhooks.container.build_receipt_handler",
                side_effect=lambda **kwargs: build_spy(
                    attempt_repository=DjangoAttemptRepository(
                        clock=kwargs["clock"]
                    ),
                    clock=kwargs["clock"],
                ),
            ),
            patch(
                "linewebhooks.container.timezone.now",
                return_value=_NOW,
            ),
        ):
            service = build_webhook_ingress_service()
        if len(spies) != 1:
            raise AssertionError("production receipt handler was not registered once")
        self.receipt_handler_spy = spies[0]
        return service, gateway

    def _create_attempt(
        self,
        capability: str,
        *,
        status: str = DeliveryAttempt.Status.SUCCEEDED,
        expires_at: datetime | None = None,
        receipt_requested: bool = True,
        channel_public_id: UUID | None = None,
        recipient_public_id: UUID | None = None,
    ) -> DeliveryAttempt:
        operation_id = uuid4()
        request_fingerprint = hashlib.sha256(
            operation_id.bytes
        ).hexdigest()
        terminal = status != DeliveryAttempt.Status.PROCESSING
        succeeded = status == DeliveryAttempt.Status.SUCCEEDED
        unsuccessful = status in (
            DeliveryAttempt.Status.FAILED,
            DeliveryAttempt.Status.UNKNOWN,
        )
        return DeliveryAttempt.objects.create(
            operation_id=operation_id,
            subject="件名",
            body="本文",
            formatted_text="【件名】\n\n本文",
            request_fingerprint=request_fingerprint,
            active_request_fingerprint=(
                None if terminal else request_fingerprint
            ),
            target_mode=DeliveryAttempt.TargetMode.LINKED_RECIPIENT,
            owner_principal_slot=1,
            owner_identity_public_id=self.identity.public_id,
            channel_public_id=(
                channel_public_id or self.channel.public_id
            ),
            channel_label_snapshot="Receipt integration",
            recipient_public_id=(
                recipient_public_id or self.recipient.public_id
            ),
            channel_active_snapshot=True,
            recipient_enabled_snapshot=True,
            friendship_state_snapshot=(
                DeliveryAttempt.FriendshipState.FRIEND
            ),
            status=status,
            failure_type=(
                DeliveryAttempt.FailureType.INVALID_REQUEST
                if status == DeliveryAttempt.Status.FAILED
                else (
                    DeliveryAttempt.FailureType.TIMEOUT_UNKNOWN
                    if status == DeliveryAttempt.Status.UNKNOWN
                    else None
                )
            ),
            line_request_id="line-request-id",
            line_accepted_request_id="line-accepted-request-id",
            accepted_at=_NOW - timedelta(minutes=1),
            processing_expires_at=_NOW + timedelta(minutes=4),
            sent_at=_NOW if succeeded else None,
            failed_at=_NOW if unsuccessful else None,
            completed_at=_NOW if terminal else None,
            receipt_requested=receipt_requested,
            receipt_expires_at=(
                expires_at or _NOW + timedelta(hours=1)
                if receipt_requested
                else None
            ),
            receipt_token_digest=(
                hashlib.sha256(capability.encode()).hexdigest()
                if receipt_requested
                else None
            ),
        )

    def _event(
        self,
        event_id: str,
        capability: str,
        *,
        redelivery: bool = False,
    ) -> dict[str, object]:
        return {
            "webhookEventId": event_id,
            "type": "postback",
            "timestamp": 100,
            "deliveryContext": {"isRedelivery": redelivery},
            "source": {
                "type": "user",
                "userId": _LINE_SUBJECT_CANARY,
            },
            "replyToken": _REPLY_TOKEN_CANARY,
            "postback": {
                "data": f"v1:delivery.received:{capability}",
            },
        }

    def _signed(
        self,
        events: list[dict[str, object]],
    ) -> tuple[bytes, str]:
        raw = json.dumps(
            {"destination": _BOT_USER_ID, "events": events},
            separators=(",", ":"),
        ).encode()
        signature = base64.b64encode(
            hmac.new(
                _CHANNEL_SECRET_CANARY.encode(),
                raw,
                hashlib.sha256,
            ).digest()
        ).decode()
        return raw, signature

    def _post(
        self,
        service: object,
        event: dict[str, object],
    ):
        raw, signature = self._signed([event])
        with (
            patch.object(
                WebhookAPIView,
                "service_factory",
                return_value=service,
            ),
            patch.object(
                WebhookAPIView,
                "monotonic_clock",
                staticmethod(service._monotonic_clock),
            ),
        ):
            return self.client.post(
                f"/api/line/webhooks/{self.channel.public_id}/",
                data=raw,
                content_type="application/json",
                HTTP_X_LINE_SIGNATURE=signature,
            )

    # テストケース: disabled・not_friend関係からprocessing・succeeded・unknown配信へ署名済みreceiptを送る
    # 期待値: 各配信だけを確認済みにし、delivery statusとLINE識別子を維持してreplyを開始しない
    def test_existing_relation_records_receipt_for_allowed_delivery_statuses(
        self,
    ) -> None:
        cases = (
            (
                DeliveryAttempt.Status.PROCESSING,
                False,
                DeliveryRecipient.FriendshipState.FRIEND,
            ),
            (
                DeliveryAttempt.Status.SUCCEEDED,
                True,
                DeliveryRecipient.FriendshipState.NOT_FRIEND,
            ),
            (
                DeliveryAttempt.Status.UNKNOWN,
                False,
                DeliveryRecipient.FriendshipState.NOT_FRIEND,
            ),
        )
        service, gateway = self._build_service()

        for index, (status, enabled, friendship) in enumerate(cases):
            with self.subTest(status=status, enabled=enabled, friendship=friendship):
                self.recipient.enabled = enabled
                self.recipient.friendship_state = friendship
                self.recipient.save(
                    update_fields=("enabled", "friendship_state")
                )
                capability = f"valid-receipt-capability-{index}"
                attempt = self._create_attempt(capability, status=status)
                event_id = f"01ARZ3NDEKTSV4RRFFQ69G{index:04d}"
                response = self._post(
                    service,
                    self._event(event_id, capability),
                )

                self.assertEqual(response.status_code, 200)
                attempt.refresh_from_db()
                self.assertEqual(attempt.receipt_confirmed_at, _NOW)
                self.assertEqual(attempt.receipt_webhook_event_id, event_id)
                self.assertEqual(attempt.status, status)
                self.assertEqual(attempt.line_request_id, "line-request-id")
                self.assertEqual(
                    attempt.line_accepted_request_id,
                    "line-accepted-request-id",
                )
                audit = InteractionAudit.objects.get(
                    webhook_event_id=event_id
                )
                self.assertEqual(audit.interaction_outcome, "action_succeeded")
                self.assertEqual(audit.reply_outcome, "not_started")

        self.assertEqual(gateway.calls, [])

    # テストケース: 期限切れ・failed・token・channel・recipient・receipt option不一致を署名済みactionへ渡す
    # 期待値: 全caseを安全なaction_rejectedへ縮約し、いずれのattemptも変更せずreplyしない
    def test_invalid_receipt_axes_are_rejected_without_mutation(
        self,
    ) -> None:
        cases = (
            {
                "name": "expired",
                "capability": "expired-capability",
                "expires_at": _NOW,
            },
            {
                "name": "failed",
                "capability": "failed-capability",
                "status": DeliveryAttempt.Status.FAILED,
            },
            {
                "name": "token",
                "capability": "stored-token-capability",
                "posted_capability": "different-token-capability",
            },
            {
                "name": "channel",
                "capability": "channel-mismatch-capability",
                "channel_public_id": uuid4(),
            },
            {
                "name": "recipient",
                "capability": "recipient-mismatch-capability",
                "recipient_public_id": uuid4(),
            },
            {
                "name": "not_requested",
                "capability": "not-requested-capability",
                "receipt_requested": False,
            },
        )
        service, gateway = self._build_service()

        for index, case in enumerate(cases, start=10):
            with self.subTest(name=case["name"]):
                capability = case["capability"]
                attempt = self._create_attempt(
                    capability,
                    status=case.get(
                        "status",
                        DeliveryAttempt.Status.SUCCEEDED,
                    ),
                    expires_at=case.get("expires_at"),
                    receipt_requested=case.get("receipt_requested", True),
                    channel_public_id=case.get("channel_public_id"),
                    recipient_public_id=case.get("recipient_public_id"),
                )
                before = (
                    attempt.status,
                    attempt.line_request_id,
                    attempt.line_accepted_request_id,
                    attempt.receipt_confirmed_at,
                    attempt.receipt_webhook_event_id,
                )
                event_id = f"01ARZ3NDEKTSV4RRFFQ69G{index:04d}"
                response = self._post(
                    service,
                    self._event(
                        event_id,
                        case.get("posted_capability", capability),
                    ),
                )

                self.assertEqual(response.status_code, 200)
                attempt.refresh_from_db()
                self.assertEqual(
                    (
                        attempt.status,
                        attempt.line_request_id,
                        attempt.line_accepted_request_id,
                        attempt.receipt_confirmed_at,
                        attempt.receipt_webhook_event_id,
                    ),
                    before,
                )
                audit = InteractionAudit.objects.get(
                    webhook_event_id=event_id
                )
                self.assertEqual(audit.interaction_outcome, "action_rejected")
                self.assertEqual(audit.reply_outcome, "not_started")

        self.assertEqual(gateway.calls, [])

    # テストケース: owner連携解除中とrecipient関係削除後に対応する署名済みreceiptを送る
    # 期待値: upstream linked-recipient保証で双方をunlinkedへ閉じ、attemptを変更せず関係を再作成しない
    def test_unlinked_owner_and_deleted_relation_are_rejected_upstream(
        self,
    ) -> None:
        owner_unlinked_capability = "owner-unlinked-capability"
        owner_unlinked_attempt = self._create_attempt(
            owner_unlinked_capability
        )
        owner = OwnerAccount.objects.get(slot=1)
        owner.state = OwnerAccount.State.DEAUTHORIZATION_PENDING
        owner.unlink_generation = uuid4()
        owner.save(update_fields=("state", "unlink_generation"))
        service, gateway = self._build_service()
        owner_event_id = "01ARZ3NDEKTSV4RRFFQ69G0020"

        owner_response = self._post(
            service,
            self._event(owner_event_id, owner_unlinked_capability),
        )

        owner_unlinked_attempt.refresh_from_db()
        self.assertEqual(owner_response.status_code, 200)
        self.assertIsNone(owner_unlinked_attempt.receipt_confirmed_at)
        self.assertIsNone(owner_unlinked_attempt.receipt_webhook_event_id)
        owner_audit = InteractionAudit.objects.get(
            webhook_event_id=owner_event_id
        )
        self.assertEqual(owner_audit.interaction_outcome, "unlinked")
        self.assertEqual(owner_audit.reply_outcome, "not_started")

        owner.state = OwnerAccount.State.ACTIVE
        owner.unlink_generation = None
        owner.save(update_fields=("state", "unlink_generation"))
        deleted_capability = "deleted-relation-capability"
        deleted_attempt = self._create_attempt(deleted_capability)
        recipient_public_id = self.recipient.public_id
        self.recipient.delete()
        deleted_event_id = "01ARZ3NDEKTSV4RRFFQ69G0021"

        deleted_response = self._post(
            service,
            self._event(deleted_event_id, deleted_capability),
        )

        self.assertEqual(deleted_response.status_code, 200)
        deleted_attempt.refresh_from_db()
        self.assertIsNone(deleted_attempt.receipt_confirmed_at)
        self.assertIsNone(deleted_attempt.receipt_webhook_event_id)
        self.assertFalse(
            DeliveryRecipient.objects.filter(
                public_id=recipient_public_id
            ).exists()
        )
        deleted_audit = InteractionAudit.objects.get(
            webhook_event_id=deleted_event_id
        )
        self.assertEqual(deleted_audit.interaction_outcome, "unlinked")
        self.assertEqual(deleted_audit.reply_outcome, "not_started")
        self.assertEqual(gateway.calls, [])

    # テストケース: 同じ署名済みreceipt eventをredeliveryとして再操作する
    # 期待値: ingress dedupがhandler再実行を防ぎ、初回確認日時・event ID・audit一件へ収束する
    def test_same_event_redelivery_converges_to_first_confirmation(
        self,
    ) -> None:
        capability = "same-event-capability"
        attempt = self._create_attempt(capability)
        service, gateway = self._build_service()
        event_id = "01ARZ3NDEKTSV4RRFFQ69G0030"

        first = self._post(service, self._event(event_id, capability))
        second = self._post(
            service,
            self._event(event_id, capability, redelivery=True),
        )

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        attempt.refresh_from_db()
        self.assertEqual(attempt.receipt_confirmed_at, _NOW)
        self.assertEqual(attempt.receipt_webhook_event_id, event_id)
        self.assertEqual(
            InteractionAudit.objects.filter(
                webhook_event_id=event_id
            ).count(),
            1,
        )
        self.assertEqual(
            WebhookEventReceipt.objects.filter(
                webhook_event_id=event_id
            ).count(),
            1,
        )
        self.assertIsInstance(
            self.receipt_handler_spy.delegate,
            ReceiptHandler,
        )
        self.assertIsInstance(
            self.receipt_handler_spy.delegate._attempt_repository,
            DjangoAttemptRepository,
        )
        self.assertEqual(len(self.receipt_handler_spy.commands), 1)
        self.assertEqual(gateway.calls, [])

    # テストケース: 同じattemptへ別event IDの署名済みreceiptを独立DB connectionから同時実行する
    # 期待値: receipt CASが初回event一件へ収束し、敗者はno-changeとなってdelivery結果を上書きしない
    def test_distinct_concurrent_events_converge_through_receipt_cas(
        self,
    ) -> None:
        capability = "parallel-receipt-capability"
        attempt = self._create_attempt(capability)
        service, gateway = self._build_service()
        event_ids = (
            "01ARZ3NDEKTSV4RRFFQ69G0031",
            "01ARZ3NDEKTSV4RRFFQ69G0032",
        )
        signed_events = [
            self._signed([self._event(event_id, capability)])
            for event_id in event_ids
        ]
        barrier = threading.Barrier(2)

        def ingest(raw: bytes, signature: str):
            connections.close_all()
            barrier.wait(timeout=5)
            try:
                return service.ingest(
                    str(self.channel.public_id),
                    raw,
                    signature,
                )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(ingest, raw, signature)
                for raw, signature in signed_events
            ]
            [future.result(timeout=10) for future in futures]

        attempt.refresh_from_db()
        first_confirmation = (
            attempt.receipt_confirmed_at,
            attempt.receipt_webhook_event_id,
        )
        self.assertEqual(first_confirmation[0], _NOW)
        self.assertIn(first_confirmation[1], event_ids)
        self.assertEqual(attempt.status, DeliveryAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.line_request_id, "line-request-id")
        self.assertEqual(
            attempt.line_accepted_request_id,
            "line-accepted-request-id",
        )
        self.assertCountEqual(
            InteractionAudit.objects.values_list(
                "interaction_outcome",
                flat=True,
            ),
            ("action_succeeded", "action_no_change"),
        )
        self.assertEqual(len(self.receipt_handler_spy.commands), 2)
        self.assertEqual(gateway.calls, [])

        third_event_id = "01ARZ3NDEKTSV4RRFFQ69G0033"
        self._post(
            service,
            self._event(third_event_id, capability),
        )
        attempt.refresh_from_db()
        self.assertEqual(
            (
                attempt.receipt_confirmed_at,
                attempt.receipt_webhook_event_id,
            ),
            first_confirmation,
        )
        self.assertEqual(
            InteractionAudit.objects.get(
                webhook_event_id=third_event_id
            ).interaction_outcome,
            "action_no_change",
        )
        self.assertEqual(len(self.receipt_handler_spy.commands), 3)

    # テストケース: 秘密canaryを含むsigned receiptをproduction compositionとsafe auditへ通す
    # 期待値: payload・LINE subject・資格情報・reply tokenをHTTP、DB、log、reprへ露出せずreply 0件となる
    def test_receipt_outcomes_and_audits_do_not_expose_sensitive_values(
        self,
    ) -> None:
        capability_canary = "raw-receipt-capability-canary"
        self._create_attempt(capability_canary)
        service, gateway = self._build_service()
        capture = _CapturingLogHandler()
        logger = logging.getLogger(f"receipt-integration-{id(self)}")
        logger.handlers = [capture]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        service._audit_logger = SafeWebhookAuditLogger(logger)
        event_id = "01ARZ3NDEKTSV4RRFFQ69G0034"

        response = self._post(
            service,
            self._event(event_id, capability_canary),
        )

        self.assertEqual(response.status_code, 200)
        audit = InteractionAudit.objects.get(webhook_event_id=event_id)
        webhook_receipt = WebhookEventReceipt.objects.get(
            webhook_event_id=event_id
        )
        surfaces = (
            response.content,
            list(DeliveryAttempt.objects.values()),
            list(InteractionAudit.objects.values()),
            list(WebhookEventReceipt.objects.values()),
            repr(audit),
            repr(webhook_receipt),
            [
                repr(command)
                for command in self.receipt_handler_spy.commands
            ],
            [record.__dict__ for record in capture.records],
            repr(gateway.calls),
        )
        rendered = repr(surfaces)
        for forbidden in (
            capability_canary,
            _LINE_SUBJECT_CANARY,
            _CHANNEL_SECRET_CANARY,
            _ACCESS_TOKEN_CANARY,
            _REPLY_TOKEN_CANARY,
            _DISPLAY_NAME_CANARY,
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(gateway.calls, [])
