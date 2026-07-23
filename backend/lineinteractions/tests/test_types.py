import json
import pickle
from dataclasses import FrozenInstanceError, fields
from typing import get_args
from uuid import uuid4

from django.apps import apps
from django.test import SimpleTestCase

from lineaccounts.types import LineSubject
from linewebhooks.types import HandlerExecutionContext

from lineinteractions.types import (
    ActionFailed,
    ActionNoChange,
    ActionRejected,
    ActionSucceeded,
    InteractionAccountDirectory,
    InteractionAuditRecord,
    InteractionAuditRepository,
    InteractionOutcome,
    InteractionParser,
    LinkedInteractionUserMissing,
    OpaqueActionPayload,
    PostbackActionCommand,
    PostbackActionHandler,
    ReplyAccepted,
    LineReplyGateway,
    ReplyOutcome,
    ReplyRejected,
    ReplyToken,
    ReplyUnknown,
    VerifiedInteractionChannel,
    VerifiedInteractionUser,
)


class _Parser:
    def parse(self, event):
        raise NotImplementedError


class _AccountDirectory:
    def resolve_linked(self, *, channel_public_id, provider_id, subject):
        return LinkedInteractionUserMissing()


class _ActionHandler:
    def handle(self, command):
        return ActionSucceeded()


class _ReplyGateway:
    def reply_text(self, *, access_token, reply_token, text, timeout):
        return ReplyAccepted()


class _AuditRepository:
    def record(self, audit):
        return "recorded"


class InteractionTypeTests(SimpleTestCase):
    # テストケース: reply tokenとopaque payloadへ秘密canaryを格納する
    # 期待値: 値は不変・redactedでpickle/JSON serializationを拒否する
    def test_sensitive_values_are_immutable_redacted_and_non_serializable(self):
        canary = "secret-canary"
        values = (ReplyToken(canary), OpaqueActionPayload(canary))

        for value in values:
            self.assertNotIn(canary, repr(value))
            self.assertNotIn(canary, str(value))
            with self.assertRaises(AttributeError):
                value.anything = "changed"
            with self.assertRaises(TypeError):
                pickle.dumps(value)
            with self.assertRaises(TypeError):
                json.dumps(value)
        for invalid in ("", "x" * 513, "\ud800"):
            with self.subTest(invalid_length=len(invalid)):
                with self.assertRaises(ValueError):
                    ReplyToken(invalid)

    # テストケース: action commandと安全なprojectionを構築する
    # 期待値: action commandのreprはpayload/subjectを隠し、reply tokenを契約に持たない
    def test_action_command_is_frozen_and_excludes_reply_token(self):
        canary = "opaque-canary"
        command = PostbackActionCommand(
            action_name="confirm",
            payload=OpaqueActionPayload(canary),
            channel=VerifiedInteractionChannel(uuid4(), "0012345678"),
            webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            user=VerifiedInteractionUser(uuid4(), uuid4()),
            execution=HandlerExecutionContext(10.0, 0, 0, 9.0),
        )

        self.assertNotIn(canary, repr(command))
        self.assertNotIn("reply_token", {field.name for field in fields(command)})
        with self.assertRaises(FrozenInstanceError):
            command.action_name = "changed"

    # テストケース: interaction/replyの有限な結果分類を参照する
    # 期待値: 設計で定義した安全な分類だけを公開する
    def test_safe_result_unions_are_finite(self):
        self.assertEqual(
            set(get_args(InteractionOutcome)),
            {
                "command_processed",
                "action_succeeded",
                "action_no_change",
                "action_rejected",
                "unknown",
                "invalid",
                "out_of_scope",
                "unlinked",
                "handler_failed",
                "processing_failed",
                "credential_unavailable",
                "deadline_exceeded",
            },
        )
        self.assertEqual(
            set(get_args(ReplyOutcome)),
            {"accepted", "rejected", "unknown", "not_started"},
        )
        self.assertEqual(
            {type(value) for value in (ActionSucceeded(), ActionNoChange(), ActionRejected(), ActionFailed())},
            {ActionSucceeded, ActionNoChange, ActionRejected, ActionFailed},
        )
        self.assertEqual(
            {type(value) for value in (ReplyAccepted(), ReplyRejected(), ReplyUnknown())},
            {ReplyAccepted, ReplyRejected, ReplyUnknown},
        )

    # テストケース: PII-free audit recordを生成する
    # 期待値: 許可済み7 fieldだけを持ち禁止データ用fieldを持たない
    def test_audit_record_has_only_safe_fields(self):
        record = InteractionAuditRecord(
            channel_public_id=uuid4(),
            webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            event_type="message",
            operation_kind="command",
            operation_identifier="connectivity_ping_v1",
            interaction_outcome="command_processed",
            reply_outcome="accepted",
        )

        self.assertEqual(
            {field.name for field in fields(record)},
            {
                "channel_public_id",
                "webhook_event_id",
                "event_type",
                "operation_kind",
                "operation_identifier",
                "interaction_outcome",
                "reply_outcome",
            },
        )
        with self.assertRaises(ValueError):
            InteractionAuditRecord(
                channel_public_id=uuid4(),
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
                event_type="message",
                operation_kind="command",
                operation_identifier=None,
                interaction_outcome="command_processed",
                reply_outcome="accepted",
            )

    # テストケース: parser/account/action/reply/auditの構造的実装を検査する
    # 期待値: 共有runtime-checkable port契約へ適合しDjango appが登録済みである
    def test_ports_and_app_registration(self):
        self.assertIsInstance(_Parser(), InteractionParser)
        self.assertIsInstance(_AccountDirectory(), InteractionAccountDirectory)
        self.assertIsInstance(_ActionHandler(), PostbackActionHandler)
        self.assertIsInstance(_ReplyGateway(), LineReplyGateway)
        self.assertIsInstance(_AuditRepository(), InteractionAuditRepository)
        self.assertEqual(apps.get_app_config("lineinteractions").name, "lineinteractions")

    # テストケース: LINE subjectを含むparsed値をreprへ変換する
    # 期待値: 生のsubject値を露出しない
    def test_line_subject_contract_remains_redacted(self):
        canary = "U" + "b" * 32
        self.assertNotIn(canary, repr(LineSubject(canary)))
