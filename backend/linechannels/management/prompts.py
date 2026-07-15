import getpass
import sys
import warnings
from collections.abc import Callable
from typing import Protocol, TextIO

from ..types import (
    ManageLineChannelInputCancelled,
    ManageLineChannelInputCollected,
    ManageLineChannelInputInvalid,
    ManageLineChannelInputResult,
    RegisterLineChannel,
    SetLineChannelActive,
    UpdateLineChannel,
)
from ..validators import (
    BoundaryValidationError,
    build_credential_pair,
    validate_bot_user_id,
    validate_label,
    validate_messaging_api_channel_id,
    validate_public_id,
)


class _InputCancelled(Exception):
    pass


class ManageLineChannelPrompts(Protocol):
    def collect(self) -> ManageLineChannelInputResult: ...


class GetPassManageLineChannelPrompts:
    def __init__(
        self,
        *,
        input_stream: TextIO | None = None,
        input_fn: Callable[[str], str] | None = None,
        getpass_fn: Callable[[str], str] | None = None,
    ) -> None:
        self._input_stream = input_stream or sys.stdin
        self._input = input_fn or input
        self._getpass = getpass_fn or getpass.getpass

    def collect(self) -> ManageLineChannelInputResult:
        if not self._input_stream.isatty():
            return ManageLineChannelInputInvalid()
        try:
            action = self._ask(
                "Action (register/update/enable/disable/cancel): "
            ).lower()
            if action == "register":
                value = self._collect_register()
            elif action == "update":
                value = self._collect_update()
            elif action in ("enable", "disable"):
                value = SetLineChannelActive(
                    validate_public_id(self._ask("Channel public UUID: ")),
                    action == "enable",
                )
            else:
                raise BoundaryValidationError()
            return ManageLineChannelInputCollected(value)
        except _InputCancelled:
            return ManageLineChannelInputCancelled()
        except (
            BoundaryValidationError,
            EOFError,
            KeyboardInterrupt,
            OSError,
            StopIteration,
            getpass.GetPassWarning,
        ):
            return ManageLineChannelInputInvalid()

    def _collect_register(self) -> RegisterLineChannel:
        channel_id = validate_messaging_api_channel_id(
            self._ask("Messaging API channel ID: ")
        )
        bot_user_id = validate_bot_user_id(self._ask("Bot user ID: "))
        label = validate_label(self._ask("Operator label: "))
        active = self._yes_no(self._ask("Initially active? (yes/no): "))
        credentials = self._collect_credentials()
        return RegisterLineChannel(
            channel_id,
            bot_user_id,
            label,
            credentials,
            active,
        )

    def _collect_update(self) -> UpdateLineChannel:
        public_id = validate_public_id(self._ask("Channel public UUID: "))
        channel_id_raw = self._ask("Messaging API channel ID (blank keeps): ")
        bot_user_id_raw = self._ask("Bot user ID (blank keeps): ")
        label_raw = self._ask("Operator label (blank keeps): ")
        replace = self._yes_no(self._ask("Replace credentials? (yes/no): "))
        state = self._ask("Active state (keep/enable/disable): ").lower()
        if state not in ("keep", "enable", "disable"):
            raise BoundaryValidationError()
        command = UpdateLineChannel(
            channel_public_id=public_id,
            messaging_api_channel_id=(
                validate_messaging_api_channel_id(channel_id_raw)
                if channel_id_raw
                else None
            ),
            bot_user_id=(
                validate_bot_user_id(bot_user_id_raw) if bot_user_id_raw else None
            ),
            label=validate_label(label_raw) if label_raw else None,
            credentials=self._collect_credentials() if replace else None,
            is_active=(
                None if state == "keep" else state == "enable"
            ),
        )
        if all(
            value is None
            for value in (
                command.messaging_api_channel_id,
                command.bot_user_id,
                command.label,
                command.credentials,
                command.is_active,
            )
        ):
            raise BoundaryValidationError()
        return command

    def _collect_credentials(self):
        access_token = self._hidden("Channel access token: ")
        if access_token != self._hidden("Confirm channel access token: "):
            raise BoundaryValidationError()
        channel_secret = self._hidden("Channel secret: ")
        if channel_secret != self._hidden("Confirm channel secret: "):
            raise BoundaryValidationError()
        return build_credential_pair(access_token, channel_secret)

    def _hidden(self, prompt: str) -> str:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = self._getpass(prompt)
        if value.strip().lower() == "cancel":
            raise _InputCancelled()
        return value

    def _ask(self, prompt: str) -> str:
        value = self._input(prompt)
        if value.strip().lower() == "cancel":
            raise _InputCancelled()
        return value

    @staticmethod
    def _yes_no(value: str) -> bool:
        normalized = value.strip().lower()
        if normalized not in ("yes", "no"):
            raise BoundaryValidationError()
        return normalized == "yes"
