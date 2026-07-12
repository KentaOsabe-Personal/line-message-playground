from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import override_settings
from rest_framework.test import APITestCase
from django.utils import timezone

from delivery.confirmation import ConfirmationTokenService
from delivery.formatters import format_message
from delivery.gateway import LINEGateway, LinePushAccepted, LinePushRejected, LinePushUnknown
from delivery.models import DeliveryAttempt


class FakeGateway:
    def __init__(self, result=None):
        self.commands = []
        self.result = result or LinePushAccepted("request-1", None)

    def push_text(self, command):
        self.commands.append(command)
        return self.result


class DeliveryApiTests(APITestCase):
    def create_processing_attempt(self, *, expires_at=None):
        message = format_message("処理中", str(uuid4()))
        now = timezone.now()
        return DeliveryAttempt.objects.create(
            operation_id=uuid4(),
            subject=message.subject,
            body=message.body,
            formatted_text=message.formatted_text,
            content_fingerprint=message.fingerprint,
            active_content_fingerprint=message.fingerprint,
            accepted_at=now,
            processing_expires_at=expires_at or now + timedelta(seconds=30),
        )

    # テストケース: 有効な件名と本文をpreviewする
    # 期待値: 正規テキストとopaqueな確認トークンだけを200で返す
    def test_preview_returns_formatted_text_and_confirmation_token(self):
        response = self.client.post(
            "/api/deliveries/preview/",
            {"subject": "件名", "body": "一行目\n二行目"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["formattedText"], "【件名】\n\n一行目\n二行目")
        self.assertEqual(set(response.data), {"formattedText", "confirmationToken"})
        self.assertNotIn("一行目", response.data["confirmationToken"])
        self.assertEqual(DeliveryAttempt.objects.count(), 0)

    # テストケース: 空白だけの件名をpreviewする
    # 期待値: 固定形式の項目別validation errorを返し、試行を作らない
    def test_preview_rejects_invalid_content_safely(self):
        response = self.client.post(
            "/api/deliveries/preview/",
            {"subject": "  ", "body": "本文"},
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
        gateway = FakeGateway()
        with patch("delivery.views.LINEGateway", return_value=gateway):
            send = self.client.post(
                "/api/deliveries/",
                {
                    "subject": "件名",
                    "body": False,
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
        self.assertEqual(gateway.commands, [])

    # テストケース: 確認済み内容を最終送信する
    # 期待値: 無認証で201成功応答を返し、安全な公開項目だけを含める
    def test_send_confirmed_content_returns_created_success(self):
        message = format_message("件名", "本文")
        token = ConfirmationTokenService().issue(message)
        gateway = FakeGateway()
        with patch("delivery.views.LINEGateway", return_value=gateway):
            response = self.client.post(
                "/api/deliveries/",
                {
                    "subject": "件名",
                    "body": "本文",
                    "operationId": str(uuid4()),
                    "confirmationToken": token,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "succeeded")
        self.assertEqual(response.data["lineRequestId"], "request-1")
        self.assertNotIn("confirmationToken", response.data)
        self.assertNotIn("target", response.data)
        self.assertEqual(len(gateway.commands), 1)

    # テストケース: 編集後の内容を古い確認トークンで送る
    # 期待値: confirmation errorを返し、DB作成とLINE呼出しを行わない
    def test_send_rejects_stale_confirmation_before_side_effects(self):
        token = ConfirmationTokenService().issue(format_message("件名", "本文"))
        gateway = FakeGateway()
        with patch("delivery.views.LINEGateway", return_value=gateway):
            response = self.client.post(
                "/api/deliveries/",
                {
                    "subject": "変更後",
                    "body": "本文",
                    "operationId": str(uuid4()),
                    "confirmationToken": token,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "confirmation_stale")
        self.assertEqual(DeliveryAttempt.objects.count(), 0)
        self.assertEqual(gateway.commands, [])

    # テストケース: 同じ操作IDを異なる内容で再利用する
    # 期待値: 409の安全なoperation_id_reusedを返し、追加送信しない
    def test_send_rejects_operation_id_reuse(self):
        operation_id = uuid4()
        gateway = FakeGateway()
        first_message = format_message("件名", "本文")
        second_message = format_message("別件", "本文")
        with patch("delivery.views.LINEGateway", return_value=gateway):
            first = self.client.post(
                "/api/deliveries/",
                {"subject": "件名", "body": "本文", "operationId": str(operation_id), "confirmationToken": ConfirmationTokenService().issue(first_message)},
                format="json",
            )
            second = self.client.post(
                "/api/deliveries/",
                {"subject": "別件", "body": "本文", "operationId": str(operation_id), "confirmationToken": ConfirmationTokenService().issue(second_message)},
                format="json",
            )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data["error"]["code"], "operation_id_reused")
        self.assertEqual(len(gateway.commands), 1)

    # テストケース: 同じ操作IDと確認済み内容を再度送信する
    # 期待値: 既存terminal結果を200で返し、LINE呼出しを増やさない
    def test_send_returns_existing_terminal_result(self):
        operation_id = uuid4()
        message = format_message("件名", "本文")
        payload = {"subject": "件名", "body": "本文", "operationId": str(operation_id), "confirmationToken": ConfirmationTokenService().issue(message)}
        gateway = FakeGateway()
        with patch("delivery.views.LINEGateway", return_value=gateway):
            first = self.client.post("/api/deliveries/", payload, format="json")
            second = self.client.post("/api/deliveries/", payload, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["status"], "succeeded")
        self.assertEqual(len(gateway.commands), 1)

    # テストケース: 別操作IDで同じ内容が処理中の間に送信する
    # 期待値: 409 delivery_in_progressを返し、LINEを呼ばない
    def test_send_rejects_same_content_while_processing(self):
        message = format_message("件名", "本文")
        now = timezone.now()
        DeliveryAttempt.objects.create(
            operation_id=uuid4(), subject=message.subject, body=message.body,
            formatted_text=message.formatted_text, content_fingerprint=message.fingerprint,
            active_content_fingerprint=message.fingerprint, accepted_at=now,
            processing_expires_at=now + timedelta(seconds=30),
        )
        gateway = FakeGateway()
        with patch("delivery.views.LINEGateway", return_value=gateway):
            response = self.client.post(
                "/api/deliveries/",
                {"subject": "件名", "body": "本文", "operationId": str(uuid4()), "confirmationToken": ConfirmationTokenService().issue(message)},
                format="json",
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "delivery_in_progress")
        self.assertEqual(gateway.commands, [])

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
        gateway = FakeGateway()
        with patch("delivery.views.LINEGateway", return_value=gateway):
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
        self.assertEqual(gateway.commands, [])

    # テストケース: gatewayが設定不足、外部拒否、timeout、unexpectedを返す。
    # 期待値: 各結果を安全なfailed/unknown応答と監査日時へ確定し、秘密値やraw errorを露出しない。
    def test_send_maps_external_failures_to_safe_terminal_responses(self):
        cases = (
            (LinePushRejected("configuration"), "failed", "configuration", "Backendの配信設定を確認してください。"),
            (LinePushRejected("invalid_request"), "failed", "invalid_request", "入力または配信設定を確認してください。"),
            (LinePushRejected("authentication"), "failed", "authentication", "LINEの認証設定を確認してください。"),
            (LinePushRejected("permission"), "failed", "permission", "LINEチャネルの権限を確認してください。"),
            (LinePushRejected("conflict"), "failed", "conflict", "LINE側で送信が競合しました。"),
            (LinePushRejected("rate_limited"), "failed", "rate_limited", "時間をおいて利用上限を確認してください。"),
            (LinePushRejected("service_unavailable"), "failed", "service_unavailable", "LINE側の状態を確認してください。"),
            (LinePushRejected("unexpected"), "failed", "unexpected", "配信結果を確定できませんでした。"),
            (LinePushUnknown("timeout_unknown"), "unknown", "timeout_unknown", "送信結果を確認できませんでした。"),
        )
        for gateway_result, expected_status, expected_code, expected_summary in cases:
            with self.subTest(code=expected_code):
                operation_id = uuid4()
                subject = f"secret-token-{operation_id}"
                body = f"raw-error-secret-user-{operation_id}"
                message = format_message(subject, body)
                gateway = FakeGateway(gateway_result)
                with patch("delivery.views.LINEGateway", return_value=gateway):
                    response = self.client.post(
                        "/api/deliveries/",
                        {
                            "subject": subject,
                            "body": body,
                            "operationId": str(operation_id),
                            "confirmationToken": ConfirmationTokenService().issue(message),
                        },
                        format="json",
                    )

                attempt = DeliveryAttempt.objects.get(operation_id=operation_id)
                rendered = str(response.data)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["status"], expected_status)
                self.assertEqual(response.data["error"]["code"], expected_code)
                self.assertEqual(response.data["error"]["summary"], expected_summary)
                self.assertIsNotNone(attempt.failed_at)
                self.assertIsNotNone(attempt.completed_at)
                self.assertEqual(attempt.failure_type, expected_code)
                self.assertNotIn(subject, rendered)
                self.assertNotIn(body, rendered)
                self.assertNotIn("secret-user", rendered)
                self.assertNotIn("raw-error", rendered)
                self.assertEqual(len(gateway.commands), 1)

    # テストケース: 実adapter境界へ秘密設定を注入し、SDKがraw情報を含む例外を送出する。
    # 期待値: API応答、DB、通常ログのいずれにもtoken、固定宛先、raw例外を露出しない。
    @override_settings(
        LINE_CHANNEL_ACCESS_TOKEN="actual-token-sentinel",
        LINE_USER_ID="actual-user-sentinel",
    )
    def test_send_does_not_expose_configuration_or_raw_gateway_error(self):
        operation_id = uuid4()
        message = format_message("件名", "本文")
        api = Mock()
        api.push_message_with_http_info.side_effect = RuntimeError(
            "actual-raw-error-sentinel"
        )
        gateway = LINEGateway(api_client_factory=lambda _: api)

        with (
            patch("delivery.views.LINEGateway", return_value=gateway),
            patch("logging.Logger._log") as log_call,
        ):
            response = self.client.post(
                "/api/deliveries/",
                {
                    "subject": message.subject,
                    "body": message.body,
                    "operationId": str(operation_id),
                    "confirmationToken": ConfirmationTokenService().issue(message),
                },
                format="json",
            )

        attempt = DeliveryAttempt.objects.get(operation_id=operation_id)
        persisted_values = " ".join(
            str(getattr(attempt, field.name))
            for field in DeliveryAttempt._meta.concrete_fields
        )
        public_output = str(response.data)
        logged_output = str(log_call.call_args_list)
        for sentinel in (
            "actual-token-sentinel",
            "actual-user-sentinel",
            "actual-raw-error-sentinel",
        ):
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, public_output)
                self.assertNotIn(sentinel, persisted_values)
                self.assertNotIn(sentinel, logged_output)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "failed")
        self.assertEqual(response.data["error"]["code"], "unexpected")
        self.assertEqual(
            response.data["error"]["summary"],
            "配信結果を確定できませんでした。",
        )
        api.push_message_with_http_info.assert_called_once()
