import socket
from dataclasses import dataclass

from django.conf import settings
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)
from linebot.v3.messaging.exceptions import ApiException


@dataclass(frozen=True)
class LinePushCommand:
    retry_key: object
    text: str


@dataclass(frozen=True)
class LinePushAccepted:
    request_id: str | None
    accepted_request_id: str | None


@dataclass(frozen=True)
class LinePushRejected:
    failure_type: str


@dataclass(frozen=True)
class LinePushUnknown:
    failure_type: str


def _header(headers, name):
    if not headers:
        return None
    lowered = name.lower()
    return next((value for key, value in headers.items() if key.lower() == lowered), None)


def is_timeout_error(error):
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(error, "reason", None)
    return isinstance(reason, (TimeoutError, socket.timeout))


class LINEGateway:
    def __init__(self, api_client_factory=None):
        self.api_client_factory = api_client_factory or self._build_api

    @staticmethod
    def _build_api(access_token):
        configuration = Configuration(access_token=access_token)
        configuration.retries = 0
        return MessagingApi(ApiClient(configuration))

    def push_text(self, command):
        access_token = getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "")
        target = getattr(settings, "LINE_USER_ID", "")
        if not access_token or not target:
            return LinePushRejected("configuration")

        api = self.api_client_factory(access_token)
        request = PushMessageRequest(
            to=target,
            messages=[TextMessage(text=command.text)],
        )
        try:
            response = api.push_message_with_http_info(
                push_message_request=request,
                x_line_retry_key=str(command.retry_key),
                _request_timeout=(3, 10),
            )
            headers = getattr(response, "headers", None)
            if headers is None and isinstance(response, tuple) and len(response) >= 3:
                headers = response[2]
            return LinePushAccepted(_header(headers, "X-Line-Request-Id"), None)
        except ApiException as error:
            return self._map_api_exception(error)
        except Exception as error:
            return self._map_unexpected(error)

    def _map_api_exception(self, error):
        status = getattr(error, "status", None)
        headers = getattr(error, "headers", None)
        accepted_id = _header(headers, "X-Line-Accepted-Request-Id")
        if status == 409 and accepted_id:
            return LinePushAccepted(None, accepted_id)
        failure_types = {
            400: "invalid_request",
            401: "authentication",
            403: "permission",
            409: "conflict",
            429: "rate_limited",
        }
        if status in failure_types:
            return LinePushRejected(failure_types[status])
        if isinstance(status, int) and 500 <= status < 600:
            return LinePushRejected("service_unavailable")
        return LinePushRejected("unexpected")

    def _map_unexpected(self, error):
        if is_timeout_error(error):
            return LinePushUnknown("timeout_unknown")
        return LinePushRejected("unexpected")
