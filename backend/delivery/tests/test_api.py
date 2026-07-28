from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.db import DatabaseError, transaction
from rest_framework.test import APIClient, APITestCase
from django.utils import timezone

from delivery.confirmation import (
    ConfirmationRejected,
    ConfirmationService,
)
from delivery.formatters import format_message, format_message_snapshot
from delivery.models import DeliveryAttempt
from delivery.types import (
    AcceptedLinkedAttempt,
    AttemptConflict,
    DeliverySnapshot,
    ExistingAttempt,
    LinkedPushExecuted,
    LinkedPushPreparation,
    LinkedPushStored,
    LinePushAccepted as LinkedLinePushAccepted,
    OwnerIdentitySnapshot,
    OwnerPrincipal as DeliveryOwnerPrincipal,
)
from lineaccounts.authentication import OWNER_SESSION_KEY
from lineaccounts.delivery_repositories import DeliveryTargetDirectory
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerAccount
from lineaccounts.repositories import DjangoAccountRepository
from lineaccounts.types import LineSubject
from linechannels.models import LineChannel

class DeliveryApiTests(APITestCase):
    def setUp(self):
        self.origin = "https://test.example.ngrok.app"
        repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = repository.lock_owner_account()
            identity = repository.upsert_identity(
                VerifiedLineIdentity(
                    "0012345678",
                    LineSubject("Usubject-secret-canary"),
                    "Owner display",
                )
            )
            owner = repository.bind_owner_identity(owner, identity.public_id)
            self.owner_session = repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )
        self.identity = LineIdentity.objects.get(public_id=identity.public_id)
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id="channel-id-secret-canary",
            bot_user_id="Ubot-secret-canary",
            label="通知チャネル",
            provider_id="0012345678",
            is_active=True,
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
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=bootstrap.cookies["csrftoken"].value,
        )
        self.client = client

    def create_processing_attempt(self, *, expires_at=None):
        message = format_message("処理中", str(uuid4()))
        now = timezone.now()
        return DeliveryAttempt.objects.create(
            operation_id=uuid4(),
            owner_principal_slot=self.owner_session.owner_slot,
            subject=message.subject,
            body=message.body,
            formatted_text=message.formatted_text,
            request_fingerprint=message.fingerprint,
            active_request_fingerprint=message.fingerprint,
            accepted_at=now,
            processing_expires_at=expires_at or now + timedelta(seconds=30),
        )

    # テストケース: active ownerが配信可能な対象と有効な内容をpreviewする
    # 期待値: 対象・整形済み内容・receipt期限とPII-freeな確認tokenをsafe summaryで返す
    def test_preview_returns_formatted_text_and_confirmation_token(self):
        expected_target = DeliveryTargetDirectory().resolve(
            self.identity.public_id,
            self.channel.public_id,
            self.recipient.public_id,
        )
        expected_message = format_message("件名", "一行目\n二行目")
        response = self.client.post(
            "/api/deliveries/preview/",
            {
                "channelId": str(self.channel.public_id),
                "recipientId": str(self.recipient.public_id),
                "subject": "件名",
                "body": "一行目\n二行目",
                "receiptRequested": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "channelId": str(self.channel.public_id),
                "channelLabel": "通知チャネル",
                "recipientId": str(self.recipient.public_id),
                "recipientDisplayName": "Owner display",
                "friendshipState": "friend",
                "formattedText": "【件名】\n\n一行目\n二行目",
                "receiptRequested": True,
                "receiptExpiresAt": response.data["receiptExpiresAt"],
                "confirmationToken": response.data["confirmationToken"],
            },
        )
        self.assertIsNotNone(response.data["receiptExpiresAt"])
        self.assertNotIn("一行目", response.data["confirmationToken"])
        decoded = ConfirmationService().decode_for_test(
            response.data["confirmationToken"]
        )
        self.assertEqual(
            decoded,
            {
                "v": 1,
                "owner": 1,
                "identity": str(self.identity.public_id),
                "channel": str(self.channel.public_id),
                "recipient": str(self.recipient.public_id),
                "target_revision": expected_target.revision.digest,
                "message_fingerprint": expected_message.fingerprint,
                "receipt_requested": True,
                "receipt_expires_at": (
                    datetime.fromisoformat(response.data["receiptExpiresAt"])
                    .astimezone(UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                ),
            },
        )
        serialized = str(response.data)
        for secret in (
            "Usubject-secret-canary",
            "channel-id-secret-canary",
            "Ubot-secret-canary",
        ):
            self.assertNotIn(secret, serialized)
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: receiptなしで配信可能な対象をpreviewする
    # 期待値: receipt期限をnullで返し、確認snapshotにもcapabilityや新しい期限を生成しない
    def test_preview_without_receipt_returns_null_expiry(self):
        expected_target = DeliveryTargetDirectory().resolve(
            self.identity.public_id,
            self.channel.public_id,
            self.recipient.public_id,
        )
        expected_message = format_message("件名", "本文")
        response = self.client.post(
            "/api/deliveries/preview/",
            {
                "channelId": str(self.channel.public_id),
                "recipientId": str(self.recipient.public_id),
                "subject": "件名",
                "body": "本文",
                "receiptRequested": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.data["receiptRequested"], False)
        self.assertIsNone(response.data["receiptExpiresAt"])
        decoded = ConfirmationService().decode_for_test(
            response.data["confirmationToken"]
        )
        self.assertEqual(
            decoded,
            {
                "v": 1,
                "owner": 1,
                "identity": str(self.identity.public_id),
                "channel": str(self.channel.public_id),
                "recipient": str(self.recipient.public_id),
                "target_revision": expected_target.revision.digest,
                "message_fingerprint": expected_message.fingerprint,
                "receipt_requested": False,
                "receipt_expires_at": None,
            },
        )
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: owner範囲外または現在配信不可のtargetでpreviewする
    # 期待値: hidden targetは404、状態不備は409へ安全に縮約しconfirmationとattemptを作らない
    def test_preview_hides_missing_target_and_rejects_live_state_change(self):
        payload = {
            "channelId": str(self.channel.public_id),
            "recipientId": str(uuid4()),
            "subject": "件名",
            "body": "本文",
            "receiptRequested": False,
        }
        hidden = self.client.post(
            "/api/deliveries/preview/",
            payload,
            format="json",
        )
        self.recipient.friendship_state = DeliveryRecipient.FriendshipState.NOT_FRIEND
        self.recipient.save(update_fields=("friendship_state", "updated_at"))
        payload["recipientId"] = str(self.recipient.public_id)
        unavailable = self.client.post(
            "/api/deliveries/preview/",
            payload,
            format="json",
        )

        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.data["error"]["code"], "target_not_available")
        self.assertEqual(unavailable.status_code, 409)
        self.assertEqual(
            unavailable.data["error"]["code"], "target_not_deliverable"
        )
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: active ownerがOriginまたはCSRF tokenなしでpreviewを要求する
    # 期待値: payload検証とtarget解決より先に403 csrf_failedで拒否する
    def test_preview_requires_exact_origin_and_csrf_before_target_resolution(self):
        self.client.credentials()
        with patch(
            "lineaccounts.delivery_repositories.DeliveryTargetDirectory.resolve",
            side_effect=AssertionError("target adapter must not run"),
        ):
            response = self.client.post(
                "/api/deliveries/preview/",
                {"secret-canary": "must-not-be-validated"},
                format="json",
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "csrf_failed")
        self.assertNotIn("secret-canary", str(response.json()))
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: preview中のtarget directory読取がDBエラーになる
    # 期待値: 固定safe 503へ縮約し、confirmation・attempt・LINE callを一切作らない
    def test_preview_target_storage_failure_has_no_side_effects(self):
        with (
            patch(
                "lineaccounts.delivery_repositories.DeliveryTargetDirectory.resolve",
                side_effect=DatabaseError("database-secret-canary"),
            ),
            patch("delivery.views.build_confirmation_service") as issue,
        ):
            response = self.client.post(
                "/api/deliveries/preview/",
                {
                    "channelId": str(self.channel.public_id),
                    "recipientId": str(self.recipient.public_id),
                    "subject": "件名",
                    "body": "本文",
                    "receiptRequested": True,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "storage_unavailable",
                    "summary": "処理を完了できませんでした。",
                }
            },
        )
        self.assertNotIn("database-secret-canary", str(response.json()))
        issue.assert_not_called()
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: 空白だけの件名をpreviewする
    # 期待値: 固定形式の項目別validation errorを返し、試行を作らない
    def test_preview_rejects_invalid_content_safely(self):
        response = self.client.post(
            "/api/deliveries/preview/",
            {
                "channelId": str(self.channel.public_id),
                "recipientId": str(self.recipient.public_id),
                "subject": "  ",
                "body": "本文",
                "receiptRequested": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertIn("subject", response.data["error"]["fields"])
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: 壊れたJSONまたは非JSON media typeでpreviewする
    # 期待値: どちらも固定の共通400 envelopeで拒否し、試行を作らない
    def test_preview_rejects_malformed_or_non_json_requests(self):
        malformed = self.client.generic(
            "POST",
            "/api/deliveries/preview/",
            '{"subject":',
            content_type="application/json",
        )
        non_json = self.client.generic(
            "POST",
            "/api/deliveries/preview/",
            "subject=件名&body=本文",
            content_type="text/plain",
        )

        for response in (malformed, non_json):
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["error"]["code"], "validation_error")
                self.assertEqual(set(response.data), {"error"})
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: 件名、本文または確認トークンへ非string JSON値を渡す
    # 期待値: 文字列へ変換せず固定400で拒否し、DBとLINEを変更しない
    def test_requests_reject_non_string_scalars_without_side_effects(self):
        preview = self.client.post(
            "/api/deliveries/preview/",
            {"subject": 123, "body": "本文"},
            format="json",
        )
        send = self.client.post(
            "/api/deliveries/",
            {
                "channelId": str(self.channel.public_id),
                "recipientId": str(self.recipient.public_id),
                "subject": "件名",
                "body": False,
                "receiptRequested": False,
                "operationId": str(uuid4()),
                "confirmationToken": 123,
            },
            format="json",
        )

        self.assertEqual(preview.status_code, 400)
        self.assertEqual(preview.data["error"]["code"], "validation_error")
        self.assertEqual(send.status_code, 400)
        self.assertEqual(send.data["error"]["code"], "validation_error")
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: 匿名利用者が不正payloadで配信APIを操作する
    # 期待値: serializerとLINE gatewayより先に全endpointを401で拒否する
    def test_anonymous_requests_are_rejected_before_validation_and_gateway(self):
        client = APIClient(enforce_csrf_checks=True)
        bootstrap = client.get("/api/account/session/")
        client.credentials(
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=bootstrap.cookies["csrftoken"].value,
        )
        responses = (
            client.post(
                "/api/deliveries/preview/",
                {"subject": [], "body": {}},
                format="json",
            ),
            client.post("/api/deliveries/", {}, format="json"),
            client.post(
                "/api/deliveries/not-a-uuid/status/", {}, format="json"
            ),
        )

        self.assertTrue(all(response.status_code == 401 for response in responses))
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: unlink pendingのownerが不正payloadでpreview・send・statusを要求する
    # 期待値: serializer・target・serviceより先に全unsafe endpointを403で拒否する
    def test_pending_owner_is_rejected_before_validation_and_gateway(self):
        OwnerAccount.objects.filter(slot=1).update(
            state=OwnerAccount.State.DEAUTHORIZATION_PENDING,
            unlink_generation=uuid4(),
        )
        operation_id = uuid4()

        with (
            patch(
                "delivery.views.LinkedPreviewRequestSerializer.is_valid",
                side_effect=AssertionError("preview serializer must not run"),
            ),
            patch(
                "delivery.views.LinkedSendDeliveryRequestSerializer.is_valid",
                side_effect=AssertionError("send serializer must not run"),
            ),
            patch(
                "delivery.views.EmptyRequestSerializer.is_valid",
                side_effect=AssertionError("status serializer must not run"),
            ),
            patch(
                "lineaccounts.delivery_repositories.DeliveryTargetDirectory.resolve",
                side_effect=AssertionError("target adapter must not run"),
            ),
            patch("delivery.views.build_delivery_service") as service_factory,
        ):
            responses = (
                self.client.post(
                    "/api/deliveries/preview/",
                    {"channelId": "invalid"},
                    format="json",
                ),
                self.client.post(
                    "/api/deliveries/",
                    {"channelId": "invalid"},
                    format="json",
                ),
                self.client.post(
                    f"/api/deliveries/{operation_id}/status/",
                    {"secretCanary": "pending-secret-canary"},
                    format="json",
                ),
            )

        self.assertTrue(all(response.status_code == 403 for response in responses))
        self.assertTrue(
            all(
                response.json()["error"]["code"]
                in ("owner_not_allowed", "owner_operation_blocked")
                for response in responses
            )
        )
        self.assertTrue(
            all(
                "pending-secret-canary" not in str(response.json())
                for response in responses
            )
        )
        self.assertEqual(DeliveryAttempt.objects.count(), 0)
        service_factory.assert_not_called()

    # テストケース: active ownerがOriginなしで配信を要求する
    # 期待値: payload処理とLINE gatewayより先に403 csrf_failedで拒否する
    def test_delivery_requires_exact_origin_and_csrf_before_handler(self):
        self.client.credentials()

        response = self.client.post("/api/deliveries/", {}, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "csrf_failed")
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: 存在しない操作IDの状態を確認する
    # 期待値: 試行を作らず安全な404を返す
    def test_status_missing_operation_returns_safe_404(self):
        response = self.client.post(
            f"/api/deliveries/{uuid4()}/status/",
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "operation_not_found")
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: UUIDでない操作IDの状態を確認する
    # 期待値: 固定形式のvalidation errorを400で返す
    def test_status_invalid_operation_id_returns_validation_error(self):
        response = self.client.post(
            "/api/deliveries/not-a-uuid/status/",
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "validation_error")

    # テストケース: 期限内と期限切れのprocessing試行を状態確認する
    # 期待値: 期限内は202、期限切れはLINEなしで200 unknownへ収束する
    def test_status_maps_processing_and_expired_attempts(self):
        processing = self.create_processing_attempt()
        expired = self.create_processing_attempt(expires_at=timezone.now() - timedelta(seconds=1))
        processing_response = self.client.post(
            f"/api/deliveries/{processing.operation_id}/status/", format="json"
        )
        expired_response = self.client.post(
            f"/api/deliveries/{expired.operation_id}/status/", format="json"
        )

        self.assertEqual(processing_response.status_code, 202)
        self.assertEqual(processing_response.data["status"], "processing")
        self.assertIn("expiresAt", processing_response.data)
        self.assertEqual(expired_response.status_code, 200)
        self.assertEqual(expired_response.data["status"], "unknown")
        self.assertEqual(expired_response.data["error"]["code"], "processing_expired")

    def _linked_snapshot(
        self,
        *,
        operation_id,
        status_value,
        completed_at=None,
        failure=None,
        receipt_status="not_requested",
        receipt_expires_at=None,
        receipt_confirmed_at=None,
        receipt_webhook_event_id=None,
    ):
        target = DeliveryTargetDirectory().resolve(
            self.identity.public_id,
            self.channel.public_id,
            self.recipient.public_id,
        )
        now = timezone.now()
        return DeliverySnapshot(
            operation_id=operation_id,
            owner=DeliveryOwnerPrincipal(self.owner_session.owner_slot),
            owner_identity=OwnerIdentitySnapshot(self.identity.public_id),
            target=target.snapshot,
            message=format_message_snapshot("件名", "本文"),
            status=status_value,
            accepted_at=now,
            completed_at=completed_at,
            line_request_id=(
                "line-request-safe"
                if status_value == "succeeded"
                else None
            ),
            line_accepted_request_id=(
                "line-accepted-internal"
                if status_value == "succeeded"
                else None
            ),
            failure=failure,
            receipt_status=receipt_status,
            receipt_expires_at=receipt_expires_at,
            receipt_confirmed_at=receipt_confirmed_at,
            receipt_webhook_event_id=receipt_webhook_event_id,
        )

    def _linked_send_payload(self, operation_id):
        preview = self.client.post(
            "/api/deliveries/preview/",
            {
                "channelId": str(self.channel.public_id),
                "recipientId": str(self.recipient.public_id),
                "subject": "件名",
                "body": "本文",
                "receiptRequested": False,
            },
            format="json",
        )
        self.assertEqual(preview.status_code, 200)
        return {
            "channelId": str(self.channel.public_id),
            "recipientId": str(self.recipient.public_id),
            "subject": "件名",
            "body": "本文",
            "receiptRequested": False,
            "operationId": str(operation_id),
            "confirmationToken": preview.data["confirmationToken"],
        }

    # テストケース: 確認済みlinked requestを新規attemptとして送信する
    # 期待値: accept、push、finalizeを各一回だけ実行し、保存済みsnapshotをsafe DTOで返す
    def test_linked_send_orchestrates_once_and_returns_stored_snapshot(self):
        operation_id = uuid4()
        payload = self._linked_send_payload(operation_id)
        target = DeliveryTargetDirectory().resolve(
            self.identity.public_id,
            self.channel.public_id,
            self.recipient.public_id,
        )
        processing = self._linked_snapshot(
            operation_id=operation_id,
            status_value="processing",
        )
        accepted = AcceptedLinkedAttempt(
            attempt_id=1,
            snapshot=processing,
            push_preparation=LinkedPushPreparation(
                target=target,
                message=format_message_snapshot("件名", "本文"),
                receipt_capability=None,
            ),
        )
        executed = LinkedPushExecuted(
            attempt_id=1,
            result=LinkedLinePushAccepted("line-request-safe", None),
        )
        terminal = self._linked_snapshot(
            operation_id=operation_id,
            status_value="succeeded",
            completed_at=timezone.now(),
        )
        service = Mock()
        service.accept_confirmed.return_value = accepted
        service.push_accepted.return_value = executed
        service.finalize_linked_push.return_value = LinkedPushStored(terminal)

        with patch(
            "delivery.views.build_delivery_service",
            return_value=service,
        ):
            response = self.client.post(
                "/api/deliveries/",
                payload,
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "succeeded")
        self.assertEqual(
            response.data["snapshot"]["channelLabel"],
            "通知チャネル",
        )
        self.assertEqual(response.data["receipt"]["status"], "not_requested")
        service.accept_confirmed.assert_called_once()
        service.push_accepted.assert_called_once_with(accepted)
        service.finalize_linked_push.assert_called_once_with(executed)
        rendered = str(response.data)
        for secret in (
            "Usubject-secret-canary",
            "channel-id-secret-canary",
            "line-accepted-internal",
            "件名",
            "本文",
        ):
            self.assertNotIn(secret, rendered)

    # テストケース: 同一operationの確認済みlinked requestを再送する
    # 期待値: 既存保存状態を返し、pushとfinalizeを呼ばず自動再送しない
    def test_linked_send_existing_attempt_never_pushes_again(self):
        operation_id = uuid4()
        payload = self._linked_send_payload(operation_id)
        terminal = self._linked_snapshot(
            operation_id=operation_id,
            status_value="succeeded",
            completed_at=timezone.now(),
        )
        service = Mock()
        service.accept_confirmed.return_value = ExistingAttempt(terminal)

        with patch("delivery.views.build_delivery_service", return_value=service):
            response = self.client.post(
                "/api/deliveries/",
                payload,
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["operationId"], str(operation_id))
        service.push_accepted.assert_not_called()
        service.finalize_linked_push.assert_not_called()

    # テストケース: preview後にmessageまたはreceipt optionを変更してsendする
    # 期待値: signed snapshot不一致を409へ縮約し、attempt受付とpushを開始しない
    def test_linked_send_rejects_changed_confirmation_axes_before_service(self):
        operation_id = uuid4()
        payload = self._linked_send_payload(operation_id)
        service = Mock()
        with patch("delivery.views.build_delivery_service", return_value=service):
            payload["body"] = "変更後本文"
            message_changed = self.client.post(
                "/api/deliveries/",
                payload,
                format="json",
            )
            payload = self._linked_send_payload(uuid4())
            payload["receiptRequested"] = True
            option_changed = self.client.post(
                "/api/deliveries/",
                payload,
                format="json",
            )

        self.assertEqual(message_changed.status_code, 409)
        self.assertEqual(
            message_changed.data["error"]["code"],
            "confirmation_stale",
        )
        self.assertEqual(option_changed.status_code, 409)
        self.assertEqual(
            option_changed.data["error"]["code"],
            "confirmation_stale",
        )
        service.accept_confirmed.assert_not_called()
        service.push_accepted.assert_not_called()

    # テストケース: ownerがcanonical operation UUIDでlinked状態を確認する
    # 期待値: session由来owner slotで照会し、snapshotと直交receiptだけをsafe DTOで返す
    def test_linked_status_uses_owner_scope_and_hides_internal_values(self):
        operation_id = uuid4()
        terminal = self._linked_snapshot(
            operation_id=operation_id,
            status_value="succeeded",
            completed_at=timezone.now(),
        )
        service = Mock()
        service.check_linked_status.return_value = terminal

        with patch("delivery.views.build_status_service", return_value=service):
            response = self.client.post(
                f"/api/deliveries/{operation_id}/status/",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        service.check_linked_status.assert_called_once_with(
            self.owner_session.owner_slot,
            operation_id,
        )
        self.assertEqual(
            response.data["snapshot"]["recipientId"],
            str(self.recipient.public_id),
        )
        rendered = str(response.data)
        self.assertNotIn(str(self.identity.public_id), rendered)
        self.assertNotIn("line-accepted-internal", rendered)

    # テストケース: 非canonical UUIDまたはowner範囲外operationの状態を確認する
    # 期待値: adapterへ曖昧なIDを渡さず、存在を開示しない固定400/404へ縮約する
    def test_linked_status_rejects_noncanonical_and_hides_unknown_operation(self):
        service = Mock()
        service.check_linked_status.return_value = None
        operation_id = uuid4()
        with patch("delivery.views.build_status_service", return_value=service):
            noncanonical = self.client.post(
                f"/api/deliveries/{str(operation_id).upper()}/status/",
                {},
                format="json",
            )
            missing = self.client.post(
                f"/api/deliveries/{operation_id}/status/",
                {},
                format="json",
            )

        self.assertEqual(noncanonical.status_code, 400)
        self.assertEqual(noncanonical.data["error"]["code"], "validation_error")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.data["error"]["code"], "operation_not_found")
        service.check_linked_status.assert_called_once_with(
            self.owner_session.owner_slot,
            operation_id,
        )

    # テストケース: status POSTへ余剰fieldを含むbodyを送る
    # 期待値: owner scope照会前に固定validation errorへ縮約し入力値を反射しない
    def test_linked_status_rejects_nonempty_body_before_lookup(self):
        operation_id = uuid4()
        service = Mock()
        with patch(
            "delivery.views.build_status_service",
            return_value=service,
        ) as service_factory:
            response = self.client.post(
                f"/api/deliveries/{operation_id}/status/",
                {"capability": "receipt-secret-canary"},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertNotIn("receipt-secret-canary", str(response.data))
        service_factory.assert_not_called()
        service.check_linked_status.assert_not_called()

    # テストケース: OriginまたはCSRF tokenなしでpreview・send・statusの全unsafe endpointへ不正payloadを送る
    # 期待値: serializer・target・serviceより先に全要求を同じ403 csrf_failedで拒否し副作用を起こさない
    def test_all_unsafe_endpoints_enforce_csrf_before_request_contracts(self):
        self.client.credentials()
        operation_id = uuid4()
        with (
            patch(
                "delivery.views.LinkedPreviewRequestSerializer.is_valid",
                side_effect=AssertionError("preview serializer must not run"),
            ),
            patch(
                "delivery.views.LinkedSendDeliveryRequestSerializer.is_valid",
                side_effect=AssertionError("send serializer must not run"),
            ),
            patch(
                "delivery.views.EmptyRequestSerializer.is_valid",
                side_effect=AssertionError("status serializer must not run"),
            ),
            patch(
                "lineaccounts.delivery_repositories.DeliveryTargetDirectory.resolve",
                side_effect=AssertionError("target adapter must not run"),
            ),
            patch("delivery.views.build_delivery_service") as service_factory,
        ):
            responses = (
                self.client.post(
                    "/api/deliveries/preview/",
                    {"secretCanary": "csrf-secret-canary"},
                    format="json",
                ),
                self.client.post(
                    "/api/deliveries/",
                    {"secretCanary": "csrf-secret-canary"},
                    format="json",
                ),
                self.client.post(
                    f"/api/deliveries/{operation_id}/status/",
                    {"secretCanary": "csrf-secret-canary"},
                    format="json",
                ),
            )

        for response in responses:
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json(),
                    {
                        "error": {
                            "code": "csrf_failed",
                            "summary": "ページを再読み込みしてください。",
                        }
                    },
                )
                self.assertNotIn("csrf-secret-canary", str(response.json()))
        service_factory.assert_not_called()
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: linked previewとsendへ欠落・余剰・型違い・非canonical UUIDを入力する
    # 期待値: 全入力を固定400 envelopeへ縮約しconfirmation・attempt・service呼出しを作らない
    def test_linked_requests_reject_strict_dto_mutations_without_side_effects(self):
        valid_preview = {
            "channelId": str(self.channel.public_id),
            "recipientId": str(self.recipient.public_id),
            "subject": "件名",
            "body": "本文",
            "receiptRequested": False,
        }
        preview_mutations = (
            {key: value for key, value in valid_preview.items() if key != "body"},
            {**valid_preview, "unknownField": "secret-dto-canary"},
            {**valid_preview, "receiptRequested": "false"},
            {**valid_preview, "channelId": str(self.channel.public_id).upper()},
            {**valid_preview, "recipientId": self.recipient.public_id.hex},
            {**valid_preview, "subject": ["件名"]},
        )
        service = Mock()
        with (
            patch("delivery.views.build_confirmation_service") as issue,
            patch(
                "delivery.views.build_delivery_service",
                return_value=service,
            ) as service_factory,
        ):
            preview_responses = tuple(
                self.client.post(
                    "/api/deliveries/preview/",
                    payload,
                    format="json",
                )
                for payload in preview_mutations
            )

            valid_send = {
                **valid_preview,
                "operationId": str(uuid4()),
                "confirmationToken": "opaque-confirmation",
            }
            send_mutations = (
                {
                    key: value
                    for key, value in valid_send.items()
                    if key != "confirmationToken"
                },
                {**valid_send, "unknownField": "secret-dto-canary"},
                {**valid_send, "operationId": uuid4().hex},
                {**valid_send, "confirmationToken": {"token": "secret-dto-canary"}},
                {**valid_send, "receiptRequested": 0},
            )
            send_responses = tuple(
                self.client.post(
                    "/api/deliveries/",
                    payload,
                    format="json",
                )
                for payload in send_mutations
            )

        for response in (*preview_responses, *send_responses):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.data["error"]["code"],
                    "validation_error",
                )
                self.assertEqual(
                    response.data["error"]["summary"],
                    "入力内容を確認してください。",
                )
                self.assertNotIn("secret-dto-canary", str(response.data))
                self.assertEqual(set(response.data), {"error"})
        issue.assert_not_called()
        service_factory.assert_not_called()
        service.accept_confirmed.assert_not_called()
        service.push_accepted.assert_not_called()
        service.finalize_linked_push.assert_not_called()
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: preview後にrecipientが隠れた状態または配信不可状態へ変わってからlinked sendする
    # 期待値: 404 target_not_availableまたは409 target_not_deliverableへ固定しattempt受付とpushを開始しない
    def test_linked_send_maps_hidden_and_stale_targets_before_service(self):
        hidden_payload = self._linked_send_payload(uuid4())
        hidden_payload["recipientId"] = str(uuid4())
        stale_payload = self._linked_send_payload(uuid4())
        self.recipient.friendship_state = DeliveryRecipient.FriendshipState.NOT_FRIEND
        self.recipient.save(update_fields=("friendship_state", "updated_at"))
        service = Mock()

        with patch(
            "delivery.views.build_delivery_service",
            return_value=service,
        ) as service_factory:
            hidden = self.client.post(
                "/api/deliveries/",
                hidden_payload,
                format="json",
            )
            stale = self.client.post(
                "/api/deliveries/",
                stale_payload,
                format="json",
            )

        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.data["error"]["code"], "target_not_available")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.data["error"]["code"], "target_not_deliverable")
        service_factory.assert_not_called()
        service.accept_confirmed.assert_not_called()
        service.push_accepted.assert_not_called()
        service.finalize_linked_push.assert_not_called()
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: linked sendのoperation IDが別requestで使用済みとserviceが分類する
    # 期待値: 固定409 operation_id_reusedを返しpush・finalizeを一度も開始しない
    def test_linked_send_maps_operation_conflict_without_push(self):
        payload = self._linked_send_payload(uuid4())
        service = Mock()
        service.accept_confirmed.return_value = AttemptConflict()

        with patch("delivery.views.build_delivery_service", return_value=service):
            response = self.client.post(
                "/api/deliveries/",
                payload,
                format="json",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data,
            {
                "error": {
                    "code": "operation_id_reused",
                    "summary": "この送信操作IDは別の内容に使用済みです。",
                }
            },
        )
        service.accept_confirmed.assert_called_once()
        service.push_accepted.assert_not_called()
        service.finalize_linked_push.assert_not_called()

    # テストケース: linked sendが保存済みprocessing・succeeded・failed・unknown分類へ収束する
    # 期待値: 状態ごとのHTTP codeと固定envelopeを返し、外部生応答・PIIを含めず再pushしない
    def test_linked_send_maps_all_stored_delivery_classifications_safely(self):
        now = timezone.now()
        cases = (
            ("processing", None, None, 202, None),
            ("succeeded", now, None, 200, None),
            ("failed", now, "configuration", 200, "Backendの配信設定を確認してください。"),
            ("failed", now, "invalid_request", 200, "入力または配信設定を確認してください。"),
            ("failed", now, "authentication", 200, "LINEの認証設定を確認してください。"),
            ("failed", now, "permission", 200, "LINEチャネルの権限を確認してください。"),
            ("failed", now, "conflict", 200, "LINE側で送信が競合しました。"),
            ("failed", now, "rate_limited", 200, "時間をおいて利用上限を確認してください。"),
            ("failed", now, "target_changed", 200, "配信結果を確定できませんでした。"),
            ("unknown", now, "service_unknown", 200, "配信結果を確定できませんでした。"),
            ("unknown", now, "timeout_unknown", 200, "送信結果を確認できませんでした。"),
            ("unknown", now, "response_unknown", 200, "配信結果を確定できませんでした。"),
            ("unknown", now, "processing_expired", 200, "処理結果を確認できませんでした。"),
        )
        for status_value, completed_at, failure, http_status, summary in cases:
            with self.subTest(status=status_value, failure=failure):
                operation_id = uuid4()
                payload = self._linked_send_payload(operation_id)
                snapshot = self._linked_snapshot(
                    operation_id=operation_id,
                    status_value=status_value,
                    completed_at=completed_at,
                    failure=failure,
                )
                service = Mock()
                service.accept_confirmed.return_value = ExistingAttempt(snapshot)

                with patch("delivery.views.build_delivery_service", return_value=service):
                    response = self.client.post(
                        "/api/deliveries/",
                        payload,
                        format="json",
                    )

                self.assertEqual(response.status_code, http_status)
                self.assertEqual(response.data["status"], status_value)
                if failure is None:
                    self.assertNotIn("error", response.data)
                else:
                    self.assertEqual(response.data["error"]["code"], failure)
                    self.assertEqual(response.data["error"]["summary"], summary)
                rendered = str(response.data)
                for forbidden in (
                    "Usubject-secret-canary",
                    "Owner display",
                    "line-accepted-internal",
                    "件名",
                    "本文",
                ):
                    self.assertNotIn(forbidden, rendered)
                service.push_accepted.assert_not_called()
                service.finalize_linked_push.assert_not_called()

    # テストケース: linked confirmationがinvalid・expired・mismatchへ分類される
    # 期待値: 固定400/409の再preview envelopeを返しattempt受付とpushを開始しない
    def test_linked_send_maps_all_confirmation_rejections_before_service(self):
        cases = (
            ("invalid", 400, "confirmation_required", "送信内容をもう一度確認してください。"),
            ("expired", 409, "confirmation_expired", "確認期限が切れています。もう一度確認してください。"),
            ("mismatch", 409, "confirmation_stale", "内容が変更されています。もう一度確認してください。"),
        )
        for reason, http_status, code, summary in cases:
            with self.subTest(reason=reason):
                payload = self._linked_send_payload(uuid4())
                service = Mock()
                with (
                    patch(
                        "delivery.views.build_confirmation_service",
                    ) as confirmation_factory,
                    patch(
                        "delivery.views.build_delivery_service",
                        return_value=service,
                    ) as service_factory,
                ):
                    confirmation_factory.return_value.verify_request.return_value = (
                        ConfirmationRejected(reason)
                    )
                    response = self.client.post(
                        "/api/deliveries/",
                        payload,
                        format="json",
                    )

                self.assertEqual(response.status_code, http_status)
                self.assertEqual(
                    response.data,
                    {"error": {"code": code, "summary": summary}},
                )
                service_factory.assert_not_called()
                service.accept_confirmed.assert_not_called()
                service.push_accepted.assert_not_called()
                service.finalize_linked_push.assert_not_called()

    # テストケース: linked sendのrepository異常または未定義service結果を受け取る
    # 期待値: 生例外を反射せず固定503へ縮約しpush・finalizeを開始しない
    def test_linked_send_maps_internal_failures_to_safe_503_without_push(self):
        cases = (
            (
                DatabaseError("database-secret-canary"),
                "storage_unavailable",
                "処理を完了できませんでした。",
            ),
            (
                object(),
                "unexpected",
                "配信処理を完了できませんでした。",
            ),
        )
        for accepted_result, code, summary in cases:
            with self.subTest(code=code):
                payload = self._linked_send_payload(uuid4())
                service = Mock()
                if isinstance(accepted_result, Exception):
                    service.accept_confirmed.side_effect = accepted_result
                else:
                    service.accept_confirmed.return_value = accepted_result

                with patch(
                    "delivery.views.build_delivery_service",
                    return_value=service,
                ):
                    response = self.client.post(
                        "/api/deliveries/",
                        payload,
                        format="json",
                    )

                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.data,
                    {"error": {"code": code, "summary": summary}},
                )
                self.assertNotIn("database-secret-canary", str(response.data))
                service.push_accepted.assert_not_called()
                service.finalize_linked_push.assert_not_called()

    # テストケース: status APIがdelivery状態とnot_requested・pending・confirmed・expired receipt状態を返す
    # 期待値: 二つの状態軸を独立した固定DTOへ写し、event ID・display name・capabilityを公開しない
    def test_linked_status_maps_terminal_and_receipt_states_orthogonally(self):
        now = timezone.now()
        delivery_cases = (
            ("processing", None, None, 202),
            ("succeeded", now, None, 200),
            ("failed", now, "permission", 200),
            ("unknown", now, "response_unknown", 200),
        )
        receipt_cases = (
            ("not_requested", None, None, None),
            ("pending", now + timedelta(hours=1), None, None),
            (
                "confirmed",
                now + timedelta(hours=1),
                now,
                "event-secret-canary",
            ),
            ("expired", now - timedelta(seconds=1), None, None),
        )
        observed_pairs = set()
        for delivery_status, completed_at, failure, expected_http_status in (
            delivery_cases
        ):
            for (
                receipt_status,
                receipt_expires_at,
                receipt_confirmed_at,
                receipt_event_id,
            ) in receipt_cases:
                with self.subTest(
                    delivery_status=delivery_status,
                    receipt_status=receipt_status,
                ):
                    operation_id = uuid4()
                    snapshot = self._linked_snapshot(
                        operation_id=operation_id,
                        status_value=delivery_status,
                        completed_at=completed_at,
                        failure=failure,
                        receipt_status=receipt_status,
                        receipt_expires_at=receipt_expires_at,
                        receipt_confirmed_at=receipt_confirmed_at,
                        receipt_webhook_event_id=receipt_event_id,
                    )
                    service = Mock()
                    service.check_linked_status.return_value = snapshot

                    with patch(
                        "delivery.views.build_status_service",
                        return_value=service,
                    ):
                        response = self.client.post(
                            f"/api/deliveries/{operation_id}/status/",
                            {},
                            format="json",
                        )

                    self.assertEqual(response.status_code, expected_http_status)
                    self.assertEqual(response.data["status"], delivery_status)
                    self.assertEqual(
                        response.data["receipt"]["requested"],
                        receipt_status != "not_requested",
                    )
                    self.assertEqual(
                        response.data["receipt"]["status"],
                        receipt_status,
                    )
                    self.assertEqual(
                        set(response.data["receipt"]),
                        {"requested", "status", "expiresAt", "confirmedAt"},
                    )
                    observed_pairs.add(
                        (
                            response.data["status"],
                            response.data["receipt"]["status"],
                        )
                    )
                    rendered = str(response.data)
                    for forbidden in (
                        "event-secret-canary",
                        "Owner display",
                        "Usubject-secret-canary",
                        "receipt-capability-canary",
                        "line-accepted-internal",
                    ):
                        self.assertNotIn(forbidden, rendered)
        self.assertEqual(
            observed_pairs,
            {
                (delivery_status, receipt_status)
                for delivery_status, *_ in delivery_cases
                for receipt_status, *_ in receipt_cases
            },
        )

    # テストケース: linked target情報を持たない旧fixed payloadで新規送信を要求する
    # 期待値: strict linked DTOとして400拒否し、fixed attemptもLINE callも作成しない
    def test_legacy_fixed_payload_cannot_create_a_new_delivery(self):
        operation_id = uuid4()
        response = self.client.post(
            "/api/deliveries/",
            {
                "subject": "legacy件名",
                "body": "legacy本文",
                "operationId": str(operation_id),
                "confirmationToken": "legacy-confirmation",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertFalse(
            DeliveryAttempt.objects.filter(operation_id=operation_id).exists()
        )

    # テストケース: migration済みの既存fixed配信をowner scoped status endpointで照会する
    # 期待値: legacy応答形と確定結果を維持しlinked snapshot・receiptを黙示追加しない
    def test_legacy_fixed_status_keeps_existing_response_contract(self):
        operation_id = uuid4()
        message = format_message("legacy件名", "legacy本文")
        now = timezone.now()
        DeliveryAttempt.objects.create(
            operation_id=operation_id,
            owner_principal_slot=self.owner_session.owner_slot,
            subject=message.subject,
            body=message.body,
            formatted_text=message.formatted_text,
            request_fingerprint=message.fingerprint,
            active_request_fingerprint=None,
            target_mode=DeliveryAttempt.TargetMode.FIXED_USER,
            status=DeliveryAttempt.Status.SUCCEEDED,
            accepted_at=now,
            processing_expires_at=now + timedelta(seconds=30),
            sent_at=now,
            completed_at=now,
            line_request_id="request-1",
        )
        checked = self.client.post(
            f"/api/deliveries/{operation_id}/status/",
            {},
            format="json",
        )

        self.assertEqual(checked.status_code, 200)
        self.assertEqual(
            set(checked.data),
            {
                "status",
                "operationId",
                "acceptedAt",
                "completedAt",
                "lineRequestId",
            },
        )
        self.assertEqual(checked.data["operationId"], str(operation_id))
        self.assertEqual(checked.data["status"], "succeeded")
        self.assertEqual(checked.data["lineRequestId"], "request-1")
        self.assertNotIn("snapshot", checked.data)
        self.assertNotIn("receipt", checked.data)
