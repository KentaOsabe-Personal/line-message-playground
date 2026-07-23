from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.test import SimpleTestCase

from linechannels.types import (
    AccessToken,
    CredentialAvailable,
    CredentialUnavailable,
    LinkableChannelSummary,
)
from lineinteractions.parsing import DefaultInteractionParser
from lineinteractions.registries import (
    StaticCommandRegistry,
    StaticPostbackActionRegistry,
)
from lineinteractions.services import DefaultInteractionService
from lineinteractions.tests.support import CHANNEL_ID, interaction_event
from lineinteractions.types import (
    ActionFailed,
    ActionNoChange,
    ActionRejected,
    ActionSucceeded,
    InteractionAuditRecord,
    LinkedInteractionUserMissing,
    PostbackActionCommand,
    ReplyAccepted,
    ReplyRejected,
    ReplyUnknown,
    VerifiedInteractionUser,
)
from linewebhooks.types import (
    HandlerExecutionContext,
    HandlerFailed,
    HandlerSucceeded,
)


@dataclass
class _ChannelDirectory:
    result: object
    calls: int = 0

    def get(self, public_id: UUID) -> object:
        self.calls += 1
        return self.result


@dataclass
class _AccountDirectory:
    result: object
    calls: int = 0

    def resolve_linked(self, **kwargs: object) -> object:
        self.calls += 1
        return self.result


@dataclass
class _CredentialRepository:
    result: object
    calls: int = 0
    channel_public_id: UUID | None = None

    def get_access_token(self, channel_public_id: UUID) -> object:
        self.calls += 1
        self.channel_public_id = channel_public_id
        return self.result


class _ReplyGateway:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def reply_text(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.result


class _AuditRepository:
    def __init__(self, result: str = "recorded") -> None:
        self.result = result
        self.records: list[InteractionAuditRecord] = []

    def record(self, audit: InteractionAuditRecord) -> str:
        self.records.append(audit)
        return self.result


class _ActionHandler:
    def __init__(self, result: object, *, raises: bool = False) -> None:
        self.result = result
        self.raises = raises
        self.commands: list[PostbackActionCommand] = []

    def handle(self, command: PostbackActionCommand) -> object:
        self.commands.append(command)
        if self.raises:
            raise RuntimeError("secret-canary")
        return self.result


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _RaisingDependency:
    def __getattr__(self, name: str) -> object:
        def raise_safe_error(*args: object, **kwargs: object) -> object:
            raise RuntimeError("secret-canary")

        return raise_safe_error


class InteractionServiceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.channel = LinkableChannelSummary(
            public_id=CHANNEL_ID,
            label="channel",
            provider_id="001234",
            is_active=True,
        )
        self.user = VerifiedInteractionUser(
            identity_public_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            recipient_public_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        )
        self.channel_directory = _ChannelDirectory(self.channel)
        self.account_directory = _AccountDirectory(self.user)
        self.credentials = _CredentialRepository(
            CredentialAvailable(AccessToken("access-token-canary"))
        )
        self.gateway = _ReplyGateway(ReplyAccepted())
        self.audit = _AuditRepository()
        self.clock = _Clock(10.0)
        self.context = HandlerExecutionContext(20.0, 0, 0, 11.0)

    def service(
        self,
        *,
        parser: object | None = None,
        channel_directory: object | None = None,
        account_directory: object | None = None,
        command_registry: object | None = None,
        action_registry: object | None = None,
        credential_repository: object | None = None,
        reply_gateway: object | None = None,
        audit_repository: object | None = None,
    ) -> DefaultInteractionService:
        return DefaultInteractionService(
            parser=parser or DefaultInteractionParser(),
            channel_directory=channel_directory or self.channel_directory,
            account_directory=account_directory or self.account_directory,
            command_registry=command_registry or StaticCommandRegistry(),
            action_registry=action_registry or StaticPostbackActionRegistry(),
            credential_repository=credential_repository or self.credentials,
            reply_gateway=reply_gateway or self.gateway,
            audit_repository=audit_repository or self.audit,
            monotonic=self.clock,
        )

    # テストケース: feature flag 除去後の固定command正常系
    # 期待値: 外部replyと安全な監査を一回ずつ実行して成功を返す
    def test_command_behavior_is_enabled_after_feature_flag_removal(self):
        result = self.service().handle(interaction_event(), self.context)

        self.assertIsInstance(result, HandlerSucceeded)

    # テストケース: invalid、対象外、未知command、未連携の各no-op
    # 期待値: 安全な分類だけを一回監査し、credentialとreplyを呼ばない
    def test_invalid_out_of_scope_unlinked_and_unknown_are_safe_noops(self):
        cases = (
            (interaction_event(source={}), "invalid"),
            (interaction_event(source={"type": "group"}), "out_of_scope"),
            (interaction_event(message={"type": "text", "text": "unknown"}), "unknown"),
        )
        for event, expected in cases:
            with self.subTest(expected=expected):
                self.setUp()
                result = self.service().handle(event, self.context)
                self.assertIsInstance(result, HandlerSucceeded)
                self.assertEqual(self.audit.records[0].interaction_outcome, expected)
                self.assertEqual(self.audit.records[0].operation_kind, "none")
                self.assertEqual(self.credentials.calls, 0)
                self.assertEqual(len(self.gateway.calls), 0)
                self.assertEqual(len(self.audit.records), 1)

        self.setUp()
        self.account_directory.result = LinkedInteractionUserMissing()
        result = self.service().handle(interaction_event(), self.context)
        self.assertIsInstance(result, HandlerSucceeded)
        self.assertEqual(self.audit.records[0].interaction_outcome, "unlinked")
        self.assertEqual(self.credentials.calls, 0)
        self.assertEqual(len(self.audit.records), 1)

    # テストケース: channel不在時の信頼境界の判定順
    # 期待値: accountとregistry以降へ進まずunlinkedを一回監査する
    def test_channel_and_linked_user_checks_precede_registry_resolution(self):
        self.channel_directory.result = None
        result = self.service().handle(interaction_event(), self.context)

        self.assertIsInstance(result, HandlerSucceeded)
        self.assertEqual(self.audit.records[0].interaction_outcome, "unlinked")
        self.assertEqual(self.account_directory.calls, 0)
        self.assertEqual(self.credentials.calls, 0)
        self.assertEqual(len(self.audit.records), 1)

    # テストケース: 登録済みaction handlerが返す有限な4結果
    # 期待値: handlerを一回だけ呼び、replyなしで各結果を一回監査する
    def test_action_results_are_dispatched_once_without_reply(self):
        cases = (
            (ActionSucceeded(), "action_succeeded", HandlerSucceeded),
            (ActionNoChange(), "action_no_change", HandlerSucceeded),
            (ActionRejected(), "action_rejected", HandlerSucceeded),
            (ActionFailed(), "handler_failed", HandlerFailed),
        )
        for action_result, expected, outcome_type in cases:
            with self.subTest(expected=expected):
                self.setUp()
                handler = _ActionHandler(action_result)
                registry = StaticPostbackActionRegistry((("confirm", handler),))
                result = self.service(action_registry=registry).handle(
                    interaction_event(event_type="postback"), self.context
                )
                self.assertIsInstance(result, outcome_type)
                self.assertEqual(len(handler.commands), 1)
                self.assertEqual(handler.commands[0].action_name, "confirm")
                self.assertEqual(self.audit.records[0].interaction_outcome, expected)
                self.assertEqual(self.audit.records[0].operation_kind, "action")
                self.assertEqual(
                    self.audit.records[0].operation_identifier, "confirm"
                )
                self.assertEqual(len(self.gateway.calls), 0)
                self.assertEqual(len(self.audit.records), 1)

    # テストケース: action handlerの例外または不正return
    # 期待値: 再実行せず一回のhandler_failed監査と安全な失敗へ縮約する
    def test_action_exception_and_invalid_return_are_safe_handler_failures(self):
        for handler in (_ActionHandler(object()), _ActionHandler(None, raises=True)):
            with self.subTest(raises=handler.raises):
                self.setUp()
                registry = StaticPostbackActionRegistry((("confirm", handler),))
                result = self.service(action_registry=registry).handle(
                    interaction_event(event_type="postback"), self.context
                )
                self.assertIsInstance(result, HandlerFailed)
                self.assertEqual(len(handler.commands), 1)
                self.assertEqual(
                    self.audit.records[0].interaction_outcome, "handler_failed"
                )
                self.assertEqual(len(self.gateway.calls), 0)
                self.assertEqual(len(self.audit.records), 1)

    # テストケース: 登録済みactionへ渡す用途限定command
    # 期待値: payload、channel、user、event ID、executionを保持しreply tokenを渡さない
    def test_action_receives_all_safe_fields_but_not_reply_token(self):
        handler = _ActionHandler(ActionSucceeded())
        registry = StaticPostbackActionRegistry((("confirm", handler),))

        self.service(action_registry=registry).handle(
            interaction_event(event_type="postback"), self.context
        )

        command = handler.commands[0]
        self.assertEqual(command.action_name, "confirm")
        self.assertEqual(command.payload.reveal_for_action(), "opaque")
        self.assertEqual(command.channel.channel_public_id, CHANNEL_ID)
        self.assertEqual(command.channel.provider_id, "001234")
        self.assertEqual(command.user, self.user)
        self.assertEqual(command.webhook_event_id, interaction_event().webhook_event_id)
        self.assertIs(command.execution, self.context)
        self.assertFalse(hasattr(command, "reply_token"))

    # テストケース: 同一channel credentialと共有deadlineを使うcommand reply
    # 期待値: 最大500ms watchdogで固定textを一回だけreplyしacceptedを監査する
    def test_command_uses_same_channel_credential_and_budget_once(self):
        result = self.service().handle(interaction_event(), self.context)

        self.assertIsInstance(result, HandlerSucceeded)
        self.assertEqual(self.credentials.channel_public_id, CHANNEL_ID)
        self.assertEqual(len(self.gateway.calls), 1)
        call = self.gateway.calls[0]
        self.assertEqual(call["text"], "pong")
        self.assertAlmostEqual(call["timeout"].total_seconds, 0.5)
        self.assertEqual(self.audit.records[0].interaction_outcome, "command_processed")
        self.assertEqual(self.audit.records[0].reply_outcome, "accepted")
        self.assertEqual(len(self.audit.records), 1)

    # テストケース: credential不在またはreply開始予算不足
    # 期待値: gatewayを呼ばず各安全な失敗を一回監査する
    def test_command_does_not_start_reply_without_credential_or_budget(self):
        self.credentials.result = CredentialUnavailable("credentials_incomplete")
        result = self.service().handle(interaction_event(), self.context)
        self.assertIsInstance(result, HandlerFailed)
        self.assertEqual(self.audit.records[0].interaction_outcome, "credential_unavailable")
        self.assertEqual(len(self.gateway.calls), 0)

        self.setUp()
        self.clock.now = 10.701
        result = self.service().handle(interaction_event(), self.context)
        self.assertIsInstance(result, HandlerFailed)
        self.assertEqual(self.audit.records[0].interaction_outcome, "deadline_exceeded")
        self.assertEqual(len(self.gateway.calls), 0)

    # テストケース: reply開始の300ms閾値の直前と直後
    # 期待値: 300ms以上では200ms watchdogで一回開始し、未満では開始しない
    def test_reply_cutoff_boundary_starts_only_with_minimum_budget(self):
        self.clock.now = 10.7
        result = self.service().handle(interaction_event(), self.context)
        self.assertIsInstance(result, HandlerSucceeded)
        self.assertEqual(len(self.gateway.calls), 1)
        self.assertAlmostEqual(
            self.gateway.calls[0]["timeout"].total_seconds,
            0.2,
        )

        self.setUp()
        self.clock.now = 10.700001
        result = self.service().handle(interaction_event(), self.context)
        self.assertIsInstance(result, HandlerFailed)
        self.assertEqual(len(self.gateway.calls), 0)
        self.assertEqual(self.audit.records[0].interaction_outcome, "deadline_exceeded")

    # テストケース: gatewayのaccepted、rejected、unknown、不正return
    # 期待値: replyを最大一回に保ち、有限なreply分類だけを一回監査する
    def test_reply_results_and_invalid_gateway_return_are_safely_classified(self):
        cases = (
            (ReplyAccepted(), "accepted", HandlerSucceeded),
            (ReplyRejected(), "rejected", HandlerFailed),
            (ReplyUnknown(), "unknown", HandlerFailed),
            (object(), "unknown", HandlerFailed),
        )
        for reply_result, expected, outcome_type in cases:
            with self.subTest(expected=expected):
                self.setUp()
                self.gateway.result = reply_result
                result = self.service().handle(interaction_event(), self.context)
                self.assertIsInstance(result, outcome_type)
                self.assertEqual(len(self.gateway.calls), 1)
                self.assertEqual(self.audit.records[0].reply_outcome, expected)
                self.assertEqual(len(self.audit.records), 1)

    # テストケース: channel dependency例外とaudit保存失敗
    # 期待値: 生例外を保持せずprocessing_failedまたは安全なhandler failureへ縮約する
    def test_dependency_and_audit_failures_return_safe_failure(self):
        self.channel_directory.result = RuntimeError("marker")
        original_get = self.channel_directory.get
        self.channel_directory.get = lambda public_id: (_ for _ in ()).throw(
            RuntimeError("secret-canary")
        )
        result = self.service().handle(interaction_event(), self.context)
        self.assertIsInstance(result, HandlerFailed)
        self.assertEqual(self.audit.records[0].interaction_outcome, "processing_failed")
        self.channel_directory.get = original_get

        self.setUp()
        self.audit.result = "failed"
        result = self.service().handle(interaction_event(), self.context)
        self.assertIsInstance(result, HandlerFailed)
        self.assertEqual(len(self.audit.records), 1)

    # テストケース: operation解決前のparser、account、両registry例外
    # 期待値: safe identifierなしのprocessing_failedを一回監査し作用を開始しない
    def test_pre_operation_dependency_exceptions_have_no_safe_identifier(self):
        dependencies = (
            {"parser": _RaisingDependency()},
            {"account_directory": _RaisingDependency()},
            {"command_registry": _RaisingDependency()},
            {
                "action_registry": _RaisingDependency(),
                "event": interaction_event(event_type="postback"),
            },
        )
        for dependency in dependencies:
            with self.subTest(keys=tuple(dependency)):
                self.setUp()
                event = dependency.pop("event", interaction_event())
                result = self.service(**dependency).handle(event, self.context)
                self.assertIsInstance(result, HandlerFailed)
                self.assertEqual(len(self.audit.records), 1)
                record = self.audit.records[0]
                self.assertEqual(record.interaction_outcome, "processing_failed")
                self.assertEqual(record.operation_kind, "none")
                self.assertIsNone(record.operation_identifier)
                self.assertEqual(self.credentials.calls, 0)
                self.assertEqual(len(self.gateway.calls), 0)

    # テストケース: command解決後のcredential例外
    # 期待値: commandのsafe identifierを残してprocessing_failedを一回監査する
    def test_post_resolution_credential_exception_keeps_safe_identifier(self):
        result = self.service(
            credential_repository=_RaisingDependency()
        ).handle(interaction_event(), self.context)

        self.assertIsInstance(result, HandlerFailed)
        self.assertEqual(len(self.audit.records), 1)
        record = self.audit.records[0]
        self.assertEqual(record.interaction_outcome, "processing_failed")
        self.assertEqual(record.operation_kind, "command")
        self.assertEqual(record.operation_identifier, "connectivity_ping_v1")
        self.assertEqual(record.reply_outcome, "not_started")
        self.assertEqual(len(self.gateway.calls), 0)

    # テストケース: gateway例外とaudit repository例外
    # 期待値: gateway開始後はunknownを一回監査し、audit例外は安全な失敗で終了する
    def test_gateway_and_audit_exceptions_are_not_retried(self):
        result = self.service(reply_gateway=_RaisingDependency()).handle(
            interaction_event(), self.context
        )
        self.assertIsInstance(result, HandlerFailed)
        self.assertEqual(len(self.audit.records), 1)
        self.assertEqual(self.audit.records[0].reply_outcome, "unknown")

        self.setUp()
        result = self.service(audit_repository=_RaisingDependency()).handle(
            interaction_event(), self.context
        )
        self.assertIsInstance(result, HandlerFailed)
        self.assertEqual(len(self.gateway.calls), 1)

    # テストケース: 未登録postback action
    # 期待値: handlerとreplyを呼ばずunknownをsafe identifierなしで一回監査する
    def test_unknown_postback_is_a_safe_noop(self):
        result = self.service().handle(
            interaction_event(event_type="postback"), self.context
        )

        self.assertIsInstance(result, HandlerSucceeded)
        self.assertEqual(len(self.gateway.calls), 0)
        self.assertEqual(len(self.audit.records), 1)
        record = self.audit.records[0]
        self.assertEqual(record.interaction_outcome, "unknown")
        self.assertEqual(record.operation_kind, "none")
        self.assertIsNone(record.operation_identifier)
