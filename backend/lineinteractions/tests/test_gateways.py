import asyncio
import json
import time
from collections.abc import Callable

import httpx
from django.test import SimpleTestCase

from linechannels.types import AccessToken

from lineinteractions.gateways import HttpxLineReplyGateway
from lineinteractions.types import (
    ReplyAccepted,
    ReplyRejected,
    ReplyTimeoutBudget,
    ReplyToken,
    ReplyUnknown,
)


class _TrackingStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes = b"response-canary") -> None:
        self.body = body
        self.read = False
        self.closed = False

    async def __aiter__(self):
        self.read = True
        yield self.body

    async def aclose(self) -> None:
        self.closed = True


class _ResponseTrackingTransport(httpx.AsyncBaseTransport):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = 0
        self.requests: list[httpx.Request] = []
        self.stream = _TrackingStream()
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.requests.append(request)
        return httpx.Response(
            self.status_code,
            headers={"X-Response-Canary": "header-canary"},
            stream=self.stream,
            request=request,
        )

    async def aclose(self) -> None:
        self.closed = True


class _FailingTransport(httpx.AsyncBaseTransport):
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise self.error

    async def aclose(self) -> None:
        self.closed = True


class _DelayedTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.calls = 0
        self.active_requests = 0
        self.cancelled = False
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.active_requests += 1
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.active_requests -= 1

    async def aclose(self) -> None:
        self.closed = True


def _reply(
    gateway: HttpxLineReplyGateway,
    *,
    access_token: str = "access-token-canary",
    reply_token: str = "reply-token-canary",
    text: str = "pong",
    timeout: float = 0.3,
):
    return gateway.reply_text(
        access_token=AccessToken(access_token),
        reply_token=ReplyToken(reply_token),
        text=text,
        timeout=ReplyTimeoutBudget(timeout),
    )


def _client_factory(
    transport: httpx.AsyncBaseTransport,
) -> Callable[..., httpx.AsyncClient]:
    def build(**kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, **kwargs)

    return build


class HttpxLineReplyGatewayTests(SimpleTestCase):
    # テストケース: 固定replyを成功応答するLINE reply endpointへ送る
    # 期待値: Bearer/token/pong一件だけを一回送信しacceptedへ分類する
    def test_sends_one_fixed_text_reply_to_fixed_endpoint(self):
        transport = _ResponseTrackingTransport(200)

        result = _reply(
            HttpxLineReplyGateway(client_factory=_client_factory(transport))
        )

        self.assertIsInstance(result, ReplyAccepted)
        self.assertEqual(transport.calls, 1)
        request = transport.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            str(request.url),
            "https://api.line.me/v2/bot/message/reply",
        )
        self.assertEqual(
            request.headers["Authorization"],
            "Bearer access-token-canary",
        )
        self.assertNotIn("X-Line-Retry-Key", request.headers)
        self.assertEqual(
            json.loads(request.content),
            {
                "replyToken": "reply-token-canary",
                "messages": [{"type": "text", "text": "pong"}],
            },
        )
        self.assertFalse(transport.stream.read)
        self.assertTrue(transport.stream.closed)
        self.assertTrue(transport.closed)

    # テストケース: LINE reply endpointが明示的な非200を返す
    # 期待値: redirect追従や再試行をせず一回の要求をrejectedへ分類する
    def test_classifies_non_200_without_redirect_or_retry(self):
        transport = _ResponseTrackingTransport(307)

        result = _reply(
            HttpxLineReplyGateway(client_factory=_client_factory(transport))
        )

        self.assertIsInstance(result, ReplyRejected)
        self.assertEqual(transport.calls, 1)
        self.assertFalse(transport.stream.read)
        self.assertTrue(transport.stream.closed)
        self.assertTrue(transport.closed)

    # テストケース: timeout以外のnetwork/protocol failureを注入する
    # 期待値: 生例外を公開せず一回の要求をunknownへ分類しclientを閉じる
    def test_classifies_transport_failures_as_unknown(self):
        errors = (
            httpx.ConnectError("network-exception-canary"),
            httpx.RemoteProtocolError("protocol-exception-canary"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                transport = _FailingTransport(error)

                result = _reply(
                    HttpxLineReplyGateway(
                        client_factory=_client_factory(transport)
                    )
                )

                self.assertIsInstance(result, ReplyUnknown)
                self.assertEqual(transport.calls, 1)
                self.assertTrue(transport.closed)
                self.assertNotIn("canary", repr(result))

    # テストケース: total watchdogより遅いtransportを実行する
    # 期待値: wall-clock上限内でcancelし、unknown・一回呼出し・資源解放へ収束する
    def test_total_watchdog_cancels_and_cleans_up_slow_transport(self):
        transport = _DelayedTransport()
        started = time.monotonic()

        result = _reply(
            HttpxLineReplyGateway(
                client_factory=_client_factory(transport)
            ),
            timeout=0.05,
        )
        elapsed = time.monotonic() - started

        self.assertIsInstance(result, ReplyUnknown)
        self.assertEqual(transport.calls, 1)
        self.assertLess(elapsed, 0.5)
        self.assertTrue(transport.cancelled)
        self.assertEqual(transport.active_requests, 0)
        self.assertTrue(transport.closed)

    # テストケース: gatewayへ設計上限を超えるtimeout budgetを渡す
    # 期待値: clientの全phase timeoutとtotal watchdogを500ms以下へ制限する
    def test_caps_http_phase_and_total_watchdog_at_half_second(self):
        transport = _ResponseTrackingTransport(200)
        observed: dict[str, object] = {}

        def client_factory(**kwargs) -> httpx.AsyncClient:
            observed.update(kwargs)
            return httpx.AsyncClient(transport=transport, **kwargs)

        result = _reply(
            HttpxLineReplyGateway(client_factory=client_factory),
            timeout=10.0,
        )

        self.assertIsInstance(result, ReplyAccepted)
        configured = observed["timeout"]
        self.assertIsInstance(configured, httpx.Timeout)
        self.assertLessEqual(configured.connect, 0.5)
        self.assertLessEqual(configured.read, 0.5)
        self.assertLessEqual(configured.write, 0.5)
        self.assertLessEqual(configured.pool, 0.5)
        self.assertIs(observed["follow_redirects"], False)

    # テストケース: 固定値以外のtextをreply transportへ渡す
    # 期待値: 外部要求を開始せず公開契約違反として拒否する
    def test_rejects_non_fixed_text_before_transport_call(self):
        transport = _ResponseTrackingTransport(200)

        with self.assertRaises(ValueError):
            _reply(
                HttpxLineReplyGateway(
                    client_factory=_client_factory(transport)
                ),
                text="not-pong",
            )

        self.assertEqual(transport.calls, 0)

    # テストケース: 既にasync event loopが動くthreadから同期portを呼ぶ
    # 期待値: coroutineやbackground taskを生成せずunknownへ安全に縮約する
    def test_running_event_loop_returns_unknown_without_starting_request(self):
        transport = _ResponseTrackingTransport(200)

        async def invoke():
            before = len(asyncio.all_tasks())
            result = _reply(
                HttpxLineReplyGateway(
                    client_factory=_client_factory(transport)
                )
            )
            after = len(asyncio.all_tasks())
            return result, before, after

        result, before, after = asyncio.run(invoke())

        self.assertIsInstance(result, ReplyUnknown)
        self.assertEqual(transport.calls, 0)
        self.assertEqual(after, before)
