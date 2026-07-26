import socket
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from linebot.v3.messaging import (
    ApiClient,
    ButtonsTemplate,
    Configuration,
    MessagingApi,
    PostbackAction,
    PushMessageRequest,
    TemplateMessage,
    TextMessage,
)
from linebot.v3.messaging.exceptions import ApiException
from urllib3.exceptions import (
    NewConnectionError,
    ProtocolError,
    TimeoutError as Urllib3TimeoutError,
)

from .types import (
    LinePushAccepted as LinkedLinePushAccepted,
    LinePushRejected as LinkedLinePushRejected,
    LinePushUnknown as LinkedLinePushUnknown,
    PushLinkedRecipientCommand,
)


_RECEIPT_ALT_TEXT = "受取確認"
_RECEIPT_TEMPLATE_TEXT = "受け取り後にボタンを押してください。"
_RECEIPT_ACTION_LABEL = "受け取りました"
_RECEIPT_ACTION_PREFIX = "v1:delivery.received:"


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


class ChannelPushGateway(Protocol):
    def push(
        self,
        command: PushLinkedRecipientCommand,
    ) -> (
        LinkedLinePushAccepted
        | LinkedLinePushRejected
        | LinkedLinePushUnknown
    ): ...


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


def _is_linked_timeout_error(error):
    for current in _linked_error_chain(error):
        if isinstance(current, (TimeoutError, socket.timeout)):
            return True
        if isinstance(current, Urllib3TimeoutError) and not isinstance(
            current,
            NewConnectionError,
        ):
            return True
    return False


def _is_connection_error(error):
    connection_errors = (
        ConnectionError,
        ConnectionRefusedError,
        ConnectionResetError,
        BrokenPipeError,
        socket.gaierror,
        NewConnectionError,
        ProtocolError,
    )
    return any(
        isinstance(current, connection_errors)
        for current in _linked_error_chain(error)
    )


def _linked_error_chain(error):
    """外部例外を文字列化せず、入れ子のreasonだけを有限回たどる。"""
    current = error
    seen = set()
    for _ in range(4):
        if id(current) in seen:
            return
        seen.add(id(current))
        yield current
        try:
            reason = getattr(current, "reason", None)
        except Exception:
            return
        if not isinstance(reason, BaseException):
            return
        current = reason


def _linked_header(headers, name):
    """外部headerから公開可能な非空文字列だけを取り出す。"""
    if not headers:
        return None
    try:
        items = headers.items()
    except Exception:
        return None
    lowered = name.lower()
    try:
        for key, value in items:
            if (
                isinstance(key, str)
                and key.lower() == lowered
                and isinstance(value, str)
                and value
            ):
                return value
    except Exception:
        return None
    return None


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


class LINEChannelPushGateway:
    """選択済みchannelとrecipientだけへ一回のpushを行うgateway。"""

    def __init__(self, api_client_factory=None):
        self.api_client_factory = api_client_factory or self._build_api

    @staticmethod
    def _build_api(access_token):
        configuration = Configuration(access_token=access_token)
        configuration.retries = 0
        return MessagingApi(ApiClient(configuration))

    @staticmethod
    def _map_response(response):
        try:
            if isinstance(response, tuple):
                if len(response) < 3:
                    return LinkedLinePushUnknown("response_unknown")
                status = response[1]
                headers = response[2]
            else:
                status = getattr(response, "status_code", None)
                headers = getattr(response, "headers", None)
        except Exception:
            return LinkedLinePushUnknown("response_unknown")

        if status == 200:
            return LinkedLinePushAccepted(
                _linked_header(headers, "X-Line-Request-Id"),
                None,
            )
        return LINEChannelPushGateway._map_http_status(
            status,
            headers,
            allow_accepted_conflict=False,
        )

    @staticmethod
    def _map_http_status(
        status,
        headers,
        *,
        allow_accepted_conflict,
    ):
        accepted_request_id = _linked_header(
            headers,
            "X-Line-Accepted-Request-Id",
        )
        if (
            allow_accepted_conflict
            and status == 409
            and accepted_request_id is not None
        ):
            return LinkedLinePushAccepted(None, accepted_request_id)

        rejected_failures = {
            400: "invalid_request",
            401: "authentication",
            403: "permission",
            409: "conflict",
            429: "rate_limited",
        }
        if status in rejected_failures:
            return LinkedLinePushRejected(rejected_failures[status])
        if isinstance(status, int) and 400 <= status < 500:
            return LinkedLinePushRejected("invalid_request")
        if isinstance(status, int) and 500 <= status < 600:
            return LinkedLinePushUnknown("service_unknown")
        return LinkedLinePushUnknown("response_unknown")

    @staticmethod
    def _map_api_exception(error):
        if _is_linked_timeout_error(error):
            return LinkedLinePushUnknown("timeout_unknown")
        if _is_connection_error(error):
            return LinkedLinePushUnknown("service_unknown")
        try:
            status = getattr(error, "status", None)
            headers = getattr(error, "headers", None)
        except Exception:
            return LinkedLinePushUnknown("response_unknown")
        return LINEChannelPushGateway._map_http_status(
            status,
            headers,
            allow_accepted_conflict=True,
        )

    @staticmethod
    def _map_unexpected(error):
        if _is_linked_timeout_error(error):
            return LinkedLinePushUnknown("timeout_unknown")
        if _is_connection_error(error):
            return LinkedLinePushUnknown("service_unknown")
        return LinkedLinePushUnknown("response_unknown")

    def push(self, command: PushLinkedRecipientCommand):
        try:
            access_token = command.access_token.reveal_for_use()
            subject = command.subject.reveal_for_identity_binding()
            messages = [TextMessage(text=command.text)]
            if command.receipt_capability is not None:
                capability = (
                    command.receipt_capability.reveal_for_push_action()
                )
                messages.append(
                    TemplateMessage(
                        altText=_RECEIPT_ALT_TEXT,
                        template=ButtonsTemplate(
                            text=_RECEIPT_TEMPLATE_TEXT,
                            actions=[
                                PostbackAction(
                                    label=_RECEIPT_ACTION_LABEL,
                                    data=(
                                        f"{_RECEIPT_ACTION_PREFIX}"
                                        f"{capability}"
                                    ),
                                )
                            ],
                        ),
                    )
                )

            api = self.api_client_factory(access_token)
            request = PushMessageRequest(to=subject, messages=messages)
            response = api.push_message_with_http_info(
                push_message_request=request,
                x_line_retry_key=str(command.operation_id),
                _request_timeout=(3, 10),
            )
            return self._map_response(response)
        except ApiException as error:
            return self._map_api_exception(error)
        except Exception as error:
            return self._map_unexpected(error)
