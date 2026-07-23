from time import monotonic

from django.test import TestCase

from lineaccounts.interaction_repositories import (
    DjangoInteractionAccountDirectory,
)
from linechannels.repositories import (
    DjangoCredentialRepository,
    DjangoLineChannelDirectory,
)
from lineinteractions.container import build_interaction_handler
from lineinteractions.gateways import HttpxLineReplyGateway
from lineinteractions.parsing import DefaultInteractionParser
from lineinteractions.registries import (
    StaticCommandRegistry,
    StaticPostbackActionRegistry,
)
from lineinteractions.repositories import DjangoInteractionAuditRepository
from lineinteractions.services import DefaultInteractionService


class InteractionContainerTests(TestCase):
    # テストケース: 空のproduction action registrationからinteraction handlerを構築する
    # 期待値: DB queryや外部I/Oなしで全concrete dependencyが一度だけ合成される
    def test_builds_concrete_handler_without_external_io(self) -> None:
        with self.assertNumQueries(0):
            handler = build_interaction_handler(monotonic_clock=monotonic)

        self.assertIsInstance(handler, DefaultInteractionService)
        self.assertIsInstance(handler._parser, DefaultInteractionParser)
        self.assertIsInstance(
            handler._channel_directory,
            DjangoLineChannelDirectory,
        )
        self.assertIsInstance(
            handler._account_directory,
            DjangoInteractionAccountDirectory,
        )
        self.assertIsInstance(handler._command_registry, StaticCommandRegistry)
        self.assertIsInstance(
            handler._action_registry,
            StaticPostbackActionRegistry,
        )
        self.assertIsInstance(
            handler._credential_repository,
            DjangoCredentialRepository,
        )
        self.assertIsInstance(handler._reply_gateway, HttpxLineReplyGateway)
        self.assertIsInstance(
            handler._audit_repository,
            DjangoInteractionAuditRepository,
        )
        self.assertIs(handler._monotonic, monotonic)

    # テストケース: 同じaction名を二重登録してinteraction handlerを構築する
    # 期待値: request受付前のbuilder段階で安全な設定エラーになる
    def test_rejects_duplicate_action_registration_at_build_time(self) -> None:
        handler = _ActionHandler()

        with self.assertRaisesRegex(ValueError, "invalid action registration"):
            build_interaction_handler(
                action_registrations=(
                    ("confirm", handler),
                    ("confirm", handler),
                ),
                monotonic_clock=monotonic,
            )


class _ActionHandler:
    def handle(self, command: object) -> object:
        raise AssertionError("startup must not execute action handlers")
