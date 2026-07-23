from uuid import uuid4

from django.test import SimpleTestCase

from linewebhooks.types import HandlerExecutionContext

from lineinteractions.registries import (
    StaticCommandRegistry,
    StaticPostbackActionRegistry,
)
from lineinteractions.types import (
    ActionSucceeded,
    OpaqueActionPayload,
    PostbackActionCommand,
    VerifiedInteractionChannel,
    VerifiedInteractionUser,
)


class _Handler:
    def __init__(self):
        self.calls = 0

    def handle(self, command):
        self.calls += 1
        return ActionSucceeded()


class InteractionRegistryTests(SimpleTestCase):
    # テストケース: 固定commandと完全一致・不一致候補を解決する
    # 期待値: /pingだけを固定identifier/pongへ解決し補正しない
    def test_command_registry_resolves_only_exact_ping(self):
        registry = StaticCommandRegistry()

        resolved = registry.resolve("/ping")
        self.assertEqual(resolved.identifier, "connectivity_ping_v1")
        self.assertEqual(resolved.reply_text, "pong")
        for candidate in ("/Ping", " /ping", "/ping ", "/ping\n", "prefix/ping"):
            with self.subTest(candidate=candidate):
                self.assertIsNone(registry.resolve(candidate))

    # テストケース: immutable action registrationsからhandlerを解決する
    # 期待値: 完全一致する一handlerだけを返し元iterable変更の影響を受けない
    def test_action_registry_uses_immutable_exact_snapshot(self):
        first = _Handler()
        registrations = [("confirm", first)]
        registry = StaticPostbackActionRegistry(registrations)
        registrations.append(("later", _Handler()))

        self.assertIs(registry.resolve("confirm"), first)
        self.assertIsNone(registry.resolve("Confirm"))
        self.assertIsNone(registry.resolve("later"))
        self.assertIsNone(registry.resolve("https://example.test"))

        command = PostbackActionCommand(
            action_name="confirm",
            payload=OpaqueActionPayload("opaque"),
            channel=VerifiedInteractionChannel(uuid4(), "0012345678"),
            webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            user=VerifiedInteractionUser(uuid4(), uuid4()),
            execution=HandlerExecutionContext(10.0, 0, 0, 9.0),
        )
        result = registry.resolve("confirm").handle(command)
        self.assertIsInstance(result, ActionSucceeded)
        self.assertEqual(first.calls, 1)

    # テストケース: unsafe名・重複名・不正handlerを登録する
    # 期待値: 部分registryを公開せず起動前ValueErrorとして拒否する
    def test_action_registry_rejects_invalid_registrations(self):
        for registrations in (
            [("Confirm", _Handler())],
            [("bad action", _Handler())],
            [("confirm", object())],
            [("confirm", _Handler()), ("confirm", _Handler())],
        ):
            with self.subTest(registrations=registrations):
                with self.assertRaises(ValueError):
                    StaticPostbackActionRegistry(registrations)

    # テストケース: production相当の空action registryを構築する
    # 期待値: 任意入力を正常な未解決として返す
    def test_empty_action_registry_is_valid(self):
        registry = StaticPostbackActionRegistry()

        self.assertIsNone(registry.resolve("confirm"))
