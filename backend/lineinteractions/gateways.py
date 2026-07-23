import asyncio
from collections.abc import Callable

import httpx

from linechannels.types import AccessToken

from .constants import CONNECTIVITY_REPLY_TEXT
from .types import (
    ReplyAccepted,
    ReplyRejected,
    ReplyResult,
    ReplyTimeoutBudget,
    ReplyToken,
    ReplyUnknown,
)

LINE_REPLY_ENDPOINT = "https://api.line.me/v2/bot/message/reply"
MAX_TOTAL_WATCHDOG_SECONDS = 0.5
AsyncClientFactory = Callable[..., httpx.AsyncClient]


class HttpxLineReplyGateway:
    def __init__(
        self,
        *,
        client_factory: AsyncClientFactory = httpx.AsyncClient,
    ) -> None:
        self._client_factory = client_factory

    def reply_text(
        self,
        *,
        access_token: AccessToken,
        reply_token: ReplyToken,
        text: str,
        timeout: ReplyTimeoutBudget,
    ) -> ReplyResult:
        if (
            not isinstance(access_token, AccessToken)
            or not isinstance(reply_token, ReplyToken)
            or not isinstance(timeout, ReplyTimeoutBudget)
            or text != CONNECTIVITY_REPLY_TEXT
        ):
            raise ValueError("invalid LINE reply request")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return ReplyUnknown()

        try:
            return asyncio.run(
                self._reply_text_once(
                    access_token=access_token,
                    reply_token=reply_token,
                    text=text,
                    timeout=timeout,
                )
            )
        except Exception:
            return ReplyUnknown()

    async def _reply_text_once(
        self,
        *,
        access_token: AccessToken,
        reply_token: ReplyToken,
        text: str,
        timeout: ReplyTimeoutBudget,
    ) -> ReplyResult:
        total_seconds = min(
            float(timeout.total_seconds),
            MAX_TOTAL_WATCHDOG_SECONDS,
        )
        http_timeout = httpx.Timeout(total_seconds)
        try:
            async with asyncio.timeout(total_seconds):
                client = self._client_factory(
                    timeout=http_timeout,
                    follow_redirects=False,
                )
                async with client:
                    request = client.build_request(
                        "POST",
                        LINE_REPLY_ENDPOINT,
                        headers={
                            "Authorization": (
                                f"Bearer {access_token.reveal_for_use()}"
                            )
                        },
                        json={
                            "replyToken": reply_token.reveal_for_reply(),
                            "messages": [{"type": "text", "text": text}],
                        },
                    )
                    response = await client.send(request, stream=True)
                    try:
                        if response.status_code == httpx.codes.OK:
                            return ReplyAccepted()
                        return ReplyRejected()
                    finally:
                        await response.aclose()
        except Exception:
            return ReplyUnknown()
