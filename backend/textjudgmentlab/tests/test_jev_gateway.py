import json
import asyncio
from unittest.mock import patch

import httpx
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from textjudgmentlab.jev_gateway import (
    JevGateway,
    JevTransportFailure,
    JevTransportSuccess,
)
from textjudgmentlab.runtime import SecretValue


class _Clock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class JevGatewayTests(SimpleTestCase):
    # テストケース: 固定endpointへJev要求を送信する。
    # 期待値: 一回だけPOSTし、秘密をheaderだけに置いて所要時間を返す。
    def test_posts_once_to_fixed_endpoint_and_returns_elapsed_time(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"model": "jev-1.13.0", "answers": {}},
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = JevGateway(
            SecretValue("secret-key"),
            client=client,
            monotonic_clock=_Clock(10.0, 10.125),
        )

        result = async_to_sync(gateway.evaluate)({"model": "jev-1.13.0"})
        async_to_sync(client.aclose)()

        self.assertIsInstance(result, JevTransportSuccess)
        assert isinstance(result, JevTransportSuccess)
        self.assertEqual(result.elapsed_ms, 125.0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            str(requests[0].url), "https://api.typesafe.ai/v1/systemone"
        )
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(requests[0].headers["authorization"], "Bearer secret-key")
        self.assertEqual(json.loads(requests[0].content), {"model": "jev-1.13.0"})

    # テストケース: 非2xx、過大body、JSON envelope不正を受け取る。
    # 期待値: 生応答を漏らさず全てtransport失敗へ縮約する。
    def test_rejects_unsafe_transport_responses(self) -> None:
        responses = (
            httpx.Response(529, text="raw-upstream-canary"),
            httpx.Response(200, content=b"x" * (128 * 1024 + 1)),
            httpx.Response(200, json=["not-an-envelope"]),
            httpx.Response(200, json={"model": "jev-1.13.0"}),
        )
        for response in responses:
            with self.subTest(response=response):
                client = httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda request: response)
                )
                gateway = JevGateway(SecretValue("secret-key"), client=client)

                result = async_to_sync(gateway.evaluate)({"model": "jev-1.13.0"})
                async_to_sync(client.aclose)()

                self.assertIsInstance(result, JevTransportFailure)
                self.assertNotIn("raw-upstream-canary", repr(result))

    # テストケース: 429、529、redirectを返す外部endpointを呼ぶ。
    # 期待値: 追従・retry・model fallbackを行わず一回で失敗する。
    def test_never_retries_redirects_or_upstream_failures(self) -> None:
        for status in (302, 429, 529):
            calls = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                return httpx.Response(
                    status,
                    headers={"location": "https://attacker.invalid/redirect"},
                    request=request,
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            gateway = JevGateway(SecretValue("secret-key"), client=client)

            result = async_to_sync(gateway.evaluate)({"model": "jev-1.13.0"})
            async_to_sync(client.aclose)()

            self.assertIsInstance(result, JevTransportFailure)
            self.assertEqual(calls, 1)

    # テストケース: 本文読取を含む全体deadlineを超える。
    # 期待値: 後着結果を待たずtimeoutを安全なtransport失敗へ変換する。
    def test_applies_one_deadline_to_response_body(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.05)
            return httpx.Response(
                200,
                json={"model": "jev-1.13.0", "answers": {}},
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("textjudgmentlab.jev_gateway._DEADLINE_SECONDS", 0.01):
            gateway = JevGateway(SecretValue("secret-key"), client=client)
            result = async_to_sync(gateway.evaluate)({"model": "jev-1.13.0"})
        async_to_sync(client.aclose)()

        self.assertEqual(result, JevTransportFailure("judge_timeout"))

    # テストケース: gateway自身が生成したclientで連続して二回評価する。
    # 期待値: 各評価が独立して一回POSTし、閉鎖済みclient例外を漏らさない。
    def test_owned_client_can_evaluate_more_than_once(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={"model": "jev-1.13.0", "answers": {}},
                request=request,
            )

        clients: list[httpx.AsyncClient] = []
        real_async_client = httpx.AsyncClient

        def client_factory() -> httpx.AsyncClient:
            client = real_async_client(transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        with patch(
            "textjudgmentlab.jev_gateway.httpx.AsyncClient", side_effect=client_factory
        ):
            gateway = JevGateway(SecretValue("secret-key"))
            first = async_to_sync(gateway.evaluate)({"model": "jev-1.13.0"})
            second = async_to_sync(gateway.evaluate)({"model": "jev-1.13.0"})

        self.assertIsInstance(first, JevTransportSuccess)
        self.assertIsInstance(second, JevTransportSuccess)
        self.assertEqual(calls, 2)
        self.assertTrue(all(client.is_closed for client in clients))

    # テストケース: response受信後の本文chunkが全体deadlineを超える。
    # 期待値: 遅いchunkを待ち続けず、安全なtransport失敗へ変換する。
    def test_deadline_covers_slow_response_chunk(self) -> None:
        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(0.05)
                yield b'{"model":"jev-1.13.0","answers":{}}'

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=SlowStream(), request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("textjudgmentlab.jev_gateway._DEADLINE_SECONDS", 0.01):
            gateway = JevGateway(SecretValue("secret-key"), client=client)
            result = async_to_sync(gateway.evaluate)({"model": "jev-1.13.0"})
        async_to_sync(client.aclose)()

        self.assertIsInstance(result, JevTransportFailure)
