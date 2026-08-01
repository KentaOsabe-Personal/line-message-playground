import re
from typing import Protocol

from .admin_types import BotIdentityReceived, BotInfoFailed, BotInfoResult
from .types import AccessToken


class _BotInfoClient(Protocol):
    def get_bot_info(self, **kwargs): ...

    def close(self) -> None: ...


class _BotInfoClientFactory(Protocol):
    def __call__(
        self, access_token: str, *, retries: int
    ) -> _BotInfoClient: ...


class _SdkBotInfoClient:
    def __init__(self, access_token: str, *, retries: int) -> None:
        from linebot.v3.messaging import ApiClient, Configuration, MessagingApi

        configuration = Configuration(access_token=access_token)
        configuration.retries = retries
        self._api_client = ApiClient(configuration)
        self._messaging_api = MessagingApi(self._api_client)

    def get_bot_info(self, **kwargs):
        return self._messaging_api.get_bot_info(**kwargs)

    def close(self) -> None:
        self._api_client.close()


def _build_sdk_client(access_token: str, *, retries: int) -> _SdkBotInfoClient:
    return _SdkBotInfoClient(access_token, retries=retries)


class DefaultLineBotInfoGateway:
    _BOT_USER_ID = re.compile(r"\AU[0-9a-f]{32}\Z")

    def __init__(
        self,
        client_factory: _BotInfoClientFactory = _build_sdk_client,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("invalid timeout")
        self._client_factory = client_factory
        self._timeout_seconds = float(timeout_seconds)

    def get_bot_identity(self, access_token: AccessToken) -> BotInfoResult:
        if not isinstance(access_token, AccessToken):
            return BotInfoFailed("line_unavailable")
        client = None
        result: BotInfoResult
        try:
            client = self._client_factory(
                access_token.reveal_for_use(),
                retries=0,
            )
            response = client.get_bot_info(
                _request_timeout=self._timeout_seconds
            )
            bot_user_id = getattr(response, "user_id", None)
            if not isinstance(bot_user_id, str) or not self._BOT_USER_ID.fullmatch(
                bot_user_id
            ):
                result = BotInfoFailed("line_unavailable")
            else:
                result = BotIdentityReceived(bot_user_id)
        except Exception as error:
            status = getattr(error, "status", None)
            if status in (401, 403):
                result = BotInfoFailed("authentication_failed")
            elif status == 429:
                result = BotInfoFailed("rate_limited")
            else:
                result = BotInfoFailed("line_unavailable")
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    result = BotInfoFailed("line_unavailable")
        return result
