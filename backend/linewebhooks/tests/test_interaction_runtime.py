import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic, perf_counter
from unittest.mock import patch

import httpx
from django.db import connections
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerAccount
from linechannels import runtime
from linechannels.crypto import FernetCredentialCipher
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialContext,
    CredentialUnavailable,
)
from lineinteractions.models import InteractionAudit
from lineinteractions.gateways import HttpxLineReplyGateway
from lineinteractions.types import (
    ActionFailed,
    ActionNoChange,
    ActionRejected,
    ActionSucceeded,
    ReplyAccepted,
    ReplyRejected,
    ReplyUnknown,
)
from linewebhooks.container import build_webhook_ingress_service
from linewebhooks.audit import SafeWebhookAuditLogger
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.views import WebhookAPIView


_PROVIDER_ID = "0012345678"
_BOT_USER_ID = "U" + "1" * 32
_LINE_USER_ID = "U" + "a" * 32
_CHANNEL_SECRET = "runtime-integration-channel-secret"
_ACCESS_TOKEN = "runtime-integration-access-token"
_ENV_TOKEN_CANARY = "fixed-environment-token-canary"
_AUTHORIZATION_CANARY = "Bearer authorization-canary"
_RAW_RESPONSE_CANARY = "raw-response-canary"
_EXCEPTION_CANARY = "raw-exception-canary"


class _RecordingReplyGateway:
    def __init__(self, results: tuple[object, ...] = (ReplyAccepted(),)) -> None:
        self._results = results
        self.calls: list[tuple[object, object, str, object]] = []
        self._lock = threading.Lock()

    def push(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("push fallback is forbidden")

    def reply_text(
        self,
        *,
        access_token: object,
        reply_token: object,
        text: str,
        timeout: object,
    ) -> object:
        with self._lock:
            self.calls.append((access_token, reply_token, text, timeout))
            index = min(len(self.calls) - 1, len(self._results) - 1)
            return self._results[index]


class _RecordingActionHandler:
    def __init__(self, result: object) -> None:
        self.result = result
        self.commands: list[object] = []
        self._lock = threading.Lock()

    def handle(self, command: object) -> object:
        with self._lock:
            self.commands.append(command)
        return self.result


class _ConflictingStateActionHandler:
    def __init__(self) -> None:
        self.expected_version = 1
        self.business_version = 2
        self.attempts = 0

    def handle(self, command: object) -> object:
        self.attempts += 1
        if self.business_version != self.expected_version:
            return ActionNoChange()
        self.business_version += 1
        return ActionSucceeded()


class _AdvancingClock:
    def __init__(self, current: float = 10.0) -> None:
        self.current = current

    def __call__(self) -> float:
        return self.current


class _ClockAdvancingGateway(_RecordingReplyGateway):
    def __init__(self, clock: _AdvancingClock, step: float) -> None:
        super().__init__()
        self.clock = clock
        self.step = step

    def reply_text(self, **kwargs: object) -> object:
        result = super().reply_text(**kwargs)
        self.clock.current += self.step
        return result


class _ExplodingGateway(_RecordingReplyGateway):
    def reply_text(self, **kwargs: object) -> object:
        with self._lock:
            self.calls.append(
                (
                    kwargs["access_token"],
                    kwargs["reply_token"],
                    kwargs["text"],
                    kwargs["timeout"],
                )
            )
        raise RuntimeError(
            f"{_AUTHORIZATION_CANARY} {_RAW_RESPONSE_CANARY} "
            f"{_EXCEPTION_CANARY}"
        )


class _CapturingLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _CountingHttpxGateway(HttpxLineReplyGateway):
    def __init__(self) -> None:
        self.calls = 0
        self.clients: list[httpx.AsyncClient] = []

        def client_factory(**kwargs: object) -> httpx.AsyncClient:
            client = httpx.AsyncClient(**kwargs)
            self.clients.append(client)
            return client

        super().__init__(client_factory=client_factory)

    def reply_text(self, **kwargs: object) -> object:
        self.calls += 1
        return super().reply_text(**kwargs)


class _SlowReplyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:
        time.sleep(0.8)
        body = b'{"marker":"raw-response-canary"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, format: str, *args: object) -> None:
        return


class _DaemonThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = False


class WebhookInteractionRuntimeTests(TransactionTestCase):
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
            label="Runtime integration",
            provider_id=_PROVIDER_ID,
            is_active=True,
        )
        cipher = FernetCredentialCipher(runtime.get_validated_keyring())
        access_token = cipher.encrypt(
            AccessToken(_ACCESS_TOKEN),
            CredentialContext(self.channel.public_id, "access_token"),
        )
        channel_secret = cipher.encrypt(
            ChannelSecret(_CHANNEL_SECRET),
            CredentialContext(self.channel.public_id, "channel_secret"),
        )
        LineChannelCredential.objects.create(
            line_channel=self.channel,
            access_token_ciphertext=access_token.ciphertext,
            channel_secret_ciphertext=channel_secret.ciphertext,
        )
        self.identity = LineIdentity.objects.create(
            provider_id=_PROVIDER_ID,
            subject=_LINE_USER_ID,
            display_name="Runtime User",
        )
        OwnerAccount.objects.update_or_create(
            slot=1,
            defaults={
                "state": OwnerAccount.State.ACTIVE,
                "identity": self.identity,
            },
        )
        DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.channel,
        )

    def _build_service(
        self,
        *,
        gateway: _RecordingReplyGateway | None = None,
        actions: tuple[tuple[str, object], ...] = (),
        monotonic_clock=monotonic,
    ):
        reply_gateway = gateway or _RecordingReplyGateway()
        with patch(
            "lineinteractions.container.HttpxLineReplyGateway",
            return_value=reply_gateway,
        ):
            service = build_webhook_ingress_service(
                action_registrations=actions,
                monotonic_clock=monotonic_clock,
            )
        return service, reply_gateway

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
                _CHANNEL_SECRET.encode(),
                raw,
                hashlib.sha256,
            ).digest()
        ).decode()
        return raw, signature

    def _message(
        self,
        event_id: str,
        text: str = "/ping",
        *,
        reply_token: str = "reply-token",
        user_id: str = _LINE_USER_ID,
        redelivery: bool = False,
    ) -> dict[str, object]:
        return {
            "webhookEventId": event_id,
            "type": "message",
            "timestamp": 100,
            "deliveryContext": {"isRedelivery": redelivery},
            "source": {"type": "user", "userId": user_id},
            "replyToken": reply_token,
            "message": {"type": "text", "text": text},
        }

    def _postback(
        self,
        event_id: str,
        data: str = "v1:confirm:opaque",
        *,
        source_type: str = "user",
        user_id: str = _LINE_USER_ID,
    ) -> dict[str, object]:
        source: dict[str, object] = {"type": source_type}
        if source_type == "user":
            source["userId"] = user_id
        return {
            "webhookEventId": event_id,
            "type": "postback",
            "timestamp": 100,
            "deliveryContext": {"isRedelivery": False},
            "source": source,
            "replyToken": "postback-reply-token",
            "postback": {"data": data},
        }

    def _post(
        self,
        service: object,
        events: list[dict[str, object]],
    ):
        raw, signature = self._signed(events)
        with patch.object(
            WebhookAPIView,
            "service_factory",
            return_value=service,
        ), patch.object(
            WebhookAPIView,
            "monotonic_clock",
            staticmethod(service._monotonic_clock),
        ):
            return self.client.post(
                f"/api/line/webhooks/{self.channel.public_id}/",
                data=raw,
                content_type="application/json",
                HTTP_X_LINE_SIGNATURE=signature,
            )

    def _post_cached(
        self,
        service: object,
        events: list[dict[str, object]],
    ):
        raw, signature = self._signed(events)
        with patch(
            "linewebhooks.container._cached_service",
            service,
        ), patch.object(
            WebhookAPIView,
            "monotonic_clock",
            staticmethod(service._monotonic_clock),
        ):
            return self.client.post(
                f"/api/line/webhooks/{self.channel.public_id}/",
                data=raw,
                content_type="application/json",
                HTTP_X_LINE_SIGNATURE=signature,
            )

    # テストケース: 連携済み利用者の署名済み/pingをruntime graphへ送る
    # 期待値: same-channel資格情報とreply tokenでpong一件を一度送り、receipt・audit・空200へ確定する
    def test_signed_ping_replies_once_and_records_safe_results(self) -> None:
        service, gateway = self._build_service()

        response = self._post(
            service,
            [self._message("01ARZ3NDEKTSV4RRFFQ69G5F01")],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(len(gateway.calls), 1)
        access_token, reply_token, text, _timeout = gateway.calls[0]
        self.assertEqual(access_token.reveal_for_use(), _ACCESS_TOKEN)
        self.assertEqual(reply_token.reveal_for_reply(), "reply-token")
        self.assertEqual(text, "pong")
        self.assertEqual(
            WebhookEventReceipt.objects.get().status,
            "processed",
        )
        audit = InteractionAudit.objects.get()
        self.assertEqual(audit.operation_identifier, "connectivity_ping_v1")
        self.assertEqual(audit.interaction_outcome, "command_processed")
        self.assertEqual(audit.reply_outcome, "accepted")

    # テストケース: unknown command・未連携user・credential欠落を個別に送る
    # 期待値: unknown/unlinkedは正常no-op、credential欠落は安全な失敗となり、いずれもreplyを開始しない
    def test_message_noop_and_credential_failure_never_start_reply(self) -> None:
        cases = (
            ("unknown", "/PING", _LINE_USER_ID, "unknown", "processed"),
            ("unlinked", "/ping", "U" + "b" * 32, "unlinked", "processed"),
        )
        for index, (name, text, user_id, outcome, receipt_status) in enumerate(cases):
            with self.subTest(name=name):
                service, gateway = self._build_service()
                event_id = f"01ARZ3NDEKTSV4RRFFQ69G5F{index + 2:02d}"
                response = self._post(
                    service,
                    [self._message(event_id, text, user_id=user_id)],
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(gateway.calls, [])
                self.assertEqual(
                    InteractionAudit.objects.get(
                        webhook_event_id=event_id
                    ).interaction_outcome,
                    outcome,
                )
                self.assertEqual(
                    WebhookEventReceipt.objects.get(
                        webhook_event_id=event_id
                    ).status,
                    receipt_status,
                )

        service, gateway = self._build_service()
        interaction = service._registry.resolve("message").handler
        interaction._credential_repository = _UnavailableCredentialRepository()
        response = self._post(
            service,
            [self._message("01ARZ3NDEKTSV4RRFFQ69G5F04")],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(
            InteractionAudit.objects.get(
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5F04"
            ).interaction_outcome,
            "credential_unavailable",
        )
        self.assertEqual(
            WebhookEventReceipt.objects.get(
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5F04"
            ).status,
            "failed",
        )

    # テストケース: reply transportのaccepted・rejected・unknownを署名済みmessageから返す
    # 期待値: 各結果をaudit/receipt/HTTPへ安全に対応付け、各tokenを一回だけ使う
    def test_reply_results_map_to_audit_receipt_and_http(self) -> None:
        results = (
            (ReplyAccepted(), "accepted", "processed"),
            (ReplyRejected(), "rejected", "failed"),
            (ReplyUnknown(), "unknown", "failed"),
        )
        for index, (result, reply_outcome, receipt_status) in enumerate(results):
            with self.subTest(reply_outcome=reply_outcome):
                gateway = _RecordingReplyGateway((result,))
                service, _ = self._build_service(gateway=gateway)
                event_id = f"01ARZ3NDEKTSV4RRFFQ69G5F{index + 5:02d}"
                response = self._post(service, [self._message(event_id)])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(gateway.calls), 1)
                audit = InteractionAudit.objects.get(
                    webhook_event_id=event_id
                )
                self.assertEqual(audit.reply_outcome, reply_outcome)
                self.assertEqual(
                    WebhookEventReceipt.objects.get(
                        webhook_event_id=event_id
                    ).status,
                    receipt_status,
                )

    # テストケース: 登録済みpostback actionの4結果を署名済みrequestから処理する
    # 期待値: actionへ一度だけ委譲し、replyなしで各audit/receipt結果へ確定する
    def test_signed_postback_maps_all_action_results(self) -> None:
        cases = (
            (ActionSucceeded(), "action_succeeded", "processed"),
            (ActionNoChange(), "action_no_change", "processed"),
            (ActionRejected(), "action_rejected", "processed"),
            (ActionFailed(), "handler_failed", "failed"),
        )
        for index, (result, outcome, receipt_status) in enumerate(cases):
            with self.subTest(outcome=outcome):
                handler = _RecordingActionHandler(result)
                service, gateway = self._build_service(
                    actions=(("confirm", handler),),
                )
                event_id = f"01ARZ3NDEKTSV4RRFFQ69G5F{index + 8:02d}"
                response = self._post(service, [self._postback(event_id)])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(handler.commands), 1)
                self.assertEqual(gateway.calls, [])
                self.assertEqual(
                    InteractionAudit.objects.get(
                        webhook_event_id=event_id
                    ).interaction_outcome,
                    outcome,
                )
                self.assertEqual(
                    WebhookEventReceipt.objects.get(
                        webhook_event_id=event_id
                    ).status,
                    receipt_status,
                )

    # テストケース: 未登録・malformed・未連携・group postbackを送る
    # 期待値: action、reply、業務mutationを起こさず安全なno-opへ確定する
    def test_invalid_or_out_of_scope_postbacks_have_no_external_effect(self) -> None:
        handler = _RecordingActionHandler(ActionSucceeded())
        cases = (
            ("unknown", "v1:other:opaque", "user", _LINE_USER_ID),
            ("malformed", "confirm", "user", _LINE_USER_ID),
            ("unsafe", "v1:../:opaque", "user", _LINE_USER_ID),
            ("unlinked", "v1:confirm:opaque", "user", "U" + "b" * 32),
            ("group", "v1:confirm:opaque", "group", _LINE_USER_ID),
            ("room", "v1:confirm:opaque", "room", _LINE_USER_ID),
        )
        for index, (name, data, source_type, user_id) in enumerate(cases):
            with self.subTest(name=name):
                service, gateway = self._build_service(
                    actions=(("confirm", handler),),
                )
                event_id = f"01ARZ3NDEKTSV4RRFFQ69G5F{index + 12:02d}"
                response = self._post(
                    service,
                    [
                        self._postback(
                            event_id,
                            data,
                            source_type=source_type,
                            user_id=user_id,
                        )
                    ],
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(gateway.calls, [])
        self.assertEqual(handler.commands, [])

    # テストケース: fake action登録済みgraphへ/ping、未知message、同一event再送を流す
    # 期待値: action追加後も固定command、未知no-op、reply token一回利用の契約が変わらない
    def test_action_registration_preserves_command_and_redelivery_contracts(
        self,
    ) -> None:
        action = _RecordingActionHandler(ActionSucceeded())
        service, gateway = self._build_service(
            actions=(("confirm", action),),
        )
        ping_id = "01ARZ3NDEKTSV4RRFFQ69G5F28"

        ping = self._post(service, [self._message(ping_id)])
        duplicate = self._post(
            service,
            [self._message(ping_id, redelivery=True)],
        )
        unknown = self._post(
            service,
            [self._message("01ARZ3NDEKTSV4RRFFQ69G5F29", "/PING")],
        )

        self.assertEqual(
            [ping.status_code, duplicate.status_code, unknown.status_code],
            [200, 200, 200],
        )
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(action.commands, [])
        self.assertEqual(
            InteractionAudit.objects.get(
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5F29"
            ).interaction_outcome,
            "unknown",
        )

    # テストケース: reply accepted後のaudit保存失敗へ同じeventを再送する
    # 期待値: 初回receipt失敗へ収束し、同じreply tokenを再利用しない
    def test_redelivery_after_audit_failure_does_not_repeat_reply(self) -> None:
        service, gateway = self._build_service()
        interaction = service._registry.resolve("message").handler
        interaction._audit_repository = _FailingAuditRepository()
        event_id = "01ARZ3NDEKTSV4RRFFQ69G5F16"

        first = self._post(service, [self._message(event_id)])
        second = self._post(
            service,
            [self._message(event_id, redelivery=True)],
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(InteractionAudit.objects.count(), 0)
        self.assertEqual(WebhookEventReceipt.objects.get().status, "failed")

    # テストケース: handler成功後のreceipt finalize失敗へ同じeventを再送する
    # 期待値: processing receiptの初回実行権を再付与せずreplyを再実行しない
    def test_redelivery_after_receipt_finalize_failure_does_not_repeat_reply(
        self,
    ) -> None:
        service, gateway = self._build_service()
        event_id = "01ARZ3NDEKTSV4RRFFQ69G5F17"

        with patch.object(
            service._receipt_repository,
            "mark_processed",
            return_value="failed",
        ):
            first = self._post(service, [self._message(event_id)])
        second = self._post(
            service,
            [self._message(event_id, redelivery=True)],
        )

        self.assertEqual(first.status_code, 503)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(WebhookEventReceipt.objects.get().status, "processing")
        self.assertEqual(InteractionAudit.objects.count(), 1)

    # テストケース: 同じevent IDを独立DB connectionから同時に受付する
    # 期待値: receipt・reply・interaction auditが各一件へ収束する
    def test_concurrent_duplicate_ping_executes_external_effect_once(self) -> None:
        service, gateway = self._build_service()
        event_id = "01ARZ3NDEKTSV4RRFFQ69G5F18"
        raw, signature = self._signed([self._message(event_id)])
        barrier = threading.Barrier(2)

        def ingest():
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
            results = [executor.submit(ingest) for _ in range(2)]
            [future.result(timeout=10) for future in results]

        self.assertEqual(WebhookEventReceipt.objects.count(), 1)
        self.assertEqual(InteractionAudit.objects.count(), 1)
        self.assertEqual(len(gateway.calls), 1)

    # テストケース: 同じsigned postbackを独立DB connectionから同時に受付する
    # 期待値: dispatcherは業務stateを推測せずactionへ一度だけ委譲し、receipt/audit各一件へ収束する
    def test_concurrent_duplicate_postback_delegates_action_once(self) -> None:
        action = _ConflictingStateActionHandler()
        service, gateway = self._build_service(
            actions=(("confirm", action),),
        )
        event_id = "01ARZ3NDEKTSV4RRFFQ69G5F30"
        raw, signature = self._signed([self._postback(event_id)])
        barrier = threading.Barrier(2)

        def ingest():
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
            futures = [executor.submit(ingest) for _ in range(2)]
            [future.result(timeout=10) for future in futures]

        self.assertEqual(action.attempts, 1)
        self.assertEqual(action.business_version, 2)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(WebhookEventReceipt.objects.count(), 1)
        self.assertEqual(InteractionAudit.objects.count(), 1)
        self.assertEqual(
            InteractionAudit.objects.get().interaction_outcome,
            "action_no_change",
        )

    # テストケース: action成功後のaudit失敗へ同じpostbackを再送する
    # 期待値: failed receiptの初回実行権を再付与せずactionを再実行しない
    def test_redelivery_after_action_audit_failure_does_not_repeat_action(
        self,
    ) -> None:
        action = _RecordingActionHandler(ActionSucceeded())
        service, _gateway = self._build_service(
            actions=(("confirm", action),),
        )
        interaction = service._registry.resolve("postback").handler
        interaction._audit_repository = _FailingAuditRepository()
        event_id = "01ARZ3NDEKTSV4RRFFQ69G5F31"

        first = self._post(service, [self._postback(event_id)])
        second = self._post(service, [self._postback(event_id)])

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(action.commands), 1)
        self.assertEqual(InteractionAudit.objects.count(), 0)
        self.assertEqual(WebhookEventReceipt.objects.get().status, "failed")

    # テストケース: canaryと動的実行候補を署名済みmessage/postbackへ流す
    # 期待値: registry完全一致以外を呼ばず、永続化・repr・HTTPへ禁止データを露出しない
    def test_canaries_and_dynamic_candidates_never_escape_or_dispatch(self) -> None:
        canary = "sensitive-canary-value"
        handler = _RecordingActionHandler(ActionSucceeded())
        service, gateway = self._build_service(
            actions=(("confirm", handler),),
        )
        candidates = (
            "SELECT * FROM secret",
            "https://example.invalid/secret",
            "../../secret",
            "module.call",
            "cοnfirm",
            "confirm-extra",
        )
        events: list[dict[str, object]] = [
            self._message(
                f"01ARZ3NDEKTSV4RRFFQ69G5F{index + 19:02d}",
                candidate,
                reply_token=f"{canary}-{index}",
            )
            for index, candidate in enumerate(candidates)
        ]
        events.append(
            self._postback(
                "01ARZ3NDEKTSV4RRFFQ69G5F25",
                f"v1:other:{canary}",
            )
        )

        response = self._post(service, events)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(handler.commands, [])
        surfaces = (
            response.content,
            list(WebhookEventReceipt.objects.values()),
            list(InteractionAudit.objects.values()),
            [repr(item) for item in gateway.calls],
        )
        self.assertNotIn(canary, repr(surfaces))

    # テストケース: 全禁止データcanaryと生transport失敗をsigned requestの各境界へ流す
    # 期待値: log/repr/audit/receipt/HTTPへ露出せず、別channel・環境token・pushへfallbackしない
    def test_all_forbidden_canaries_are_redacted_without_fallbacks(self) -> None:
        second_channel = LineChannel.objects.create(
            messaging_api_channel_id="9876543210",
            bot_user_id="U" + "2" * 32,
            label="Fallback forbidden",
            provider_id=_PROVIDER_ID,
            is_active=True,
        )
        cipher = FernetCredentialCipher(runtime.get_validated_keyring())
        second_access = cipher.encrypt(
            AccessToken("other-channel-token-canary"),
            CredentialContext(second_channel.public_id, "access_token"),
        )
        second_secret = cipher.encrypt(
            ChannelSecret("other-channel-secret-canary"),
            CredentialContext(second_channel.public_id, "channel_secret"),
        )
        LineChannelCredential.objects.create(
            line_channel=second_channel,
            access_token_ciphertext=second_access.ciphertext,
            channel_secret_ciphertext=second_secret.ciphertext,
        )
        gateway = _ExplodingGateway()
        service, _ = self._build_service(gateway=gateway)
        log_handler = _CapturingLogHandler()
        logger = logging.getLogger(f"interaction-runtime-{id(self)}")
        logger.handlers = [log_handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        service._audit_logger = SafeWebhookAuditLogger(logger)
        event_id = "01ARZ3NDEKTSV4RRFFQ69G5F32"

        with self.settings(LINE_CHANNEL_ACCESS_TOKEN=_ENV_TOKEN_CANARY):
            response = self._post(
                service,
                [
                    self._message(
                        event_id,
                        "/ping",
                        reply_token="reply-token-canary",
                        user_id=_LINE_USER_ID,
                    )
                ],
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(gateway.calls), 1)
        used_access_token = gateway.calls[0][0]
        self.assertEqual(used_access_token.reveal_for_use(), _ACCESS_TOKEN)
        audit = InteractionAudit.objects.get(webhook_event_id=event_id)
        self.assertEqual(audit.reply_outcome, "unknown")
        receipt = WebhookEventReceipt.objects.get(webhook_event_id=event_id)
        self.assertEqual(receipt.status, "failed")
        log_surfaces = [record.__dict__ for record in log_handler.records]
        surfaces = (
            response.content,
            repr(audit),
            repr(receipt),
            list(InteractionAudit.objects.values()),
            list(WebhookEventReceipt.objects.values()),
            log_surfaces,
        )
        forbidden = (
            "/ping",
            "reply-token-canary",
            _LINE_USER_ID,
            _ACCESS_TOKEN,
            "other-channel-token-canary",
            _ENV_TOKEN_CANARY,
            _AUTHORIZATION_CANARY,
            _RAW_RESPONSE_CANARY,
            _EXCEPTION_CANARY,
        )
        rendered = repr(surfaces)
        for canary in forbidden:
            self.assertNotIn(canary, rendered)

        action = _RecordingActionHandler(RuntimeError(_EXCEPTION_CANARY))
        action_service, _ = self._build_service(
            actions=(("confirm", action),),
        )
        postback_response = self._post(
            action_service,
            [
                self._postback(
                    "01ARZ3NDEKTSV4RRFFQ69G5F33",
                    "v1:confirm:postback-payload-canary",
                )
            ],
        )
        self.assertEqual(postback_response.status_code, 200)
        persisted = repr(
            (
                list(InteractionAudit.objects.values()),
                list(WebhookEventReceipt.objects.values()),
                postback_response.content,
            )
        )
        self.assertNotIn("postback-payload-canary", persisted)
        self.assertNotIn(_EXCEPTION_CANARY, persisted)

    # テストケース: 1・5・10件の署名済み/pingをcached相当graphで処理する
    # 期待値: 各requestが2秒未満で完了し、eventごとにreply/audit/receipt一件となる
    def test_one_five_and_ten_ping_events_meet_two_second_contract(self) -> None:
        query_counts: dict[int, int] = {}
        for count in (1, 5, 10):
            with self.subTest(count=count):
                WebhookEventReceipt.objects.all().delete()
                InteractionAudit.objects.all().delete()
                service, gateway = self._build_service()
                events = [
                    self._message(
                        f"01ARZ3NDEKTSV4RRFFQ69G5P{index:02d}"
                    )
                    for index in range(count)
                ]
                started = perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    response = self._post_cached(service, events)
                elapsed = perf_counter() - started

                self.assertEqual(response.status_code, 200)
                self.assertLess(elapsed, 2.0)
                self.assertEqual(len(gateway.calls), count)
                self.assertEqual(WebhookEventReceipt.objects.count(), count)
                self.assertEqual(InteractionAudit.objects.count(), count)
                query_counts[count] = len(queries)
                self.assertLessEqual(
                    len(queries),
                    {1: 13, 5: 53, 10: 103}[count],
                )

        self.assertEqual(query_counts[5] - query_counts[1], 10 * 4)
        self.assertEqual(query_counts[10] - query_counts[5], 10 * 5)

    # テストケース: 先行replyが共有deadlineを消費する10件requestを処理する
    # 期待値: 予算不足後は新しいreplyを開始せず、残eventを期限超過receiptへ確定する
    def test_reply_budget_exhaustion_closes_dispatch_without_late_work(self) -> None:
        clock = _AdvancingClock()
        gateway = _ClockAdvancingGateway(clock, 0.5)
        service, _ = self._build_service(
            gateway=gateway,
            monotonic_clock=clock,
        )
        events = [
            self._message(f"01ARZ3NDEKTSV4RRFFQ69G5Q{index:02d}")
            for index in range(10)
        ]

        response = self._post(service, events)

        self.assertEqual(response.status_code, 200)
        self.assertLess(len(gateway.calls), 10)
        self.assertGreater(len(gateway.calls), 0)
        self.assertEqual(
            WebhookEventReceipt.objects.filter(status="failed").count(),
            10 - len(gateway.calls),
        )
        self.assertGreater(
            WebhookEventReceipt.objects.filter(
                status="failed",
                failure_code="dispatch_deadline_exceeded",
            ).count(),
            0,
        )

    # テストケース: controlled loopback serverがwatchdogより遅く応答するsigned /pingを処理する
    # 期待値: full requestは2秒未満でunknown/failed/単一replyへ収束し、clientを閉じて遅延taskを残さない
    def test_slow_loopback_reply_is_bounded_and_cleans_up_transport(self) -> None:
        threads_before = {thread.ident for thread in threading.enumerate()}
        server = _DaemonThreadingHTTPServer(
            ("127.0.0.1", 0),
            _SlowReplyHandler,
        )
        server_thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )
        server_thread.start()
        gateway = _CountingHttpxGateway()
        with patch(
            "lineinteractions.container.HttpxLineReplyGateway",
            return_value=gateway,
        ):
            service = build_webhook_ingress_service()
        endpoint = (
            f"http://127.0.0.1:{server.server_address[1]}/v2/bot/message/reply"
        )
        try:
            started = perf_counter()
            with patch("lineinteractions.gateways.LINE_REPLY_ENDPOINT", endpoint):
                response = self._post_cached(
                    service,
                    [self._message("01ARZ3NDEKTSV4RRFFQ69G5F34")],
                )
            elapsed = perf_counter() - started
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 2.0)
        self.assertEqual(gateway.calls, 1)
        self.assertTrue(gateway.clients)
        self.assertTrue(all(client.is_closed for client in gateway.clients))
        self.assertEqual(InteractionAudit.objects.get().reply_outcome, "unknown")
        self.assertEqual(WebhookEventReceipt.objects.get().status, "failed")
        self.assertEqual(
            {
                thread.ident
                for thread in threading.enumerate()
                if thread.ident not in threads_before
            },
            set(),
        )

    # テストケース: actionと既存friendshipを同じruntime graphで同期処理する
    # 期待値: 各local portionが100ms以内で完了し、HTTP応答後に追加作用がない
    def test_action_and_friendship_local_paths_stay_within_budget(self) -> None:
        action = _RecordingActionHandler(ActionSucceeded())
        service, gateway = self._build_service(
            actions=(("confirm", action),),
        )
        cases = (
            self._postback("01ARZ3NDEKTSV4RRFFQ69G5F35"),
            {
                "webhookEventId": "01ARZ3NDEKTSV4RRFFQ69G5F36",
                "type": "follow",
                "timestamp": 101,
                "deliveryContext": {"isRedelivery": False},
                "source": {"type": "user", "userId": _LINE_USER_ID},
            },
        )

        elapsed_values: list[float] = []
        for event in cases:
            started = perf_counter()
            response = self._post_cached(service, [event])
            elapsed_values.append(perf_counter() - started)
            self.assertEqual(response.status_code, 200)

        effects_at_response = (
            len(action.commands),
            len(gateway.calls),
            InteractionAudit.objects.count(),
            WebhookEventReceipt.objects.count(),
        )
        threads_at_response = {thread.ident for thread in threading.enumerate()}
        time.sleep(0.05)
        self.assertTrue(all(elapsed < 0.1 for elapsed in elapsed_values))
        self.assertEqual(len(action.commands), 1)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(
            effects_at_response,
            (
                len(action.commands),
                len(gateway.calls),
                InteractionAudit.objects.count(),
                WebhookEventReceipt.objects.count(),
            ),
        )
        self.assertEqual(
            {thread.ident for thread in threading.enumerate()},
            threads_at_response,
        )


class _UnavailableCredentialRepository:
    def get_access_token(self, channel_public_id: object) -> CredentialUnavailable:
        return CredentialUnavailable("credentials_incomplete")


class _FailingAuditRepository:
    def record(self, audit: object) -> str:
        return "failed"
