from django.test import SimpleTestCase

from linewebhooks.handlers import StaticHandlerRegistry
from linewebhooks.types import HandlerSucceeded, VerifiedWebhookEvent


class _StubHandler:
    def handle(self, event: VerifiedWebhookEvent) -> HandlerSucceeded:
        return HandlerSucceeded()


class StaticHandlerRegistryTests(SimpleTestCase):
    # テストケース: 異なる event type の同期 handler を起動時に登録して解決する
    # 期待値: 登録済み type は対応する一件だけを返し、未登録 type は None を返す
    def test_resolves_exactly_one_registered_handler(self) -> None:
        message_handler = _StubHandler()
        follow_handler = _StubHandler()
        registry = StaticHandlerRegistry(
            (("message", message_handler), ("follow", follow_handler))
        )

        self.assertIs(registry.resolve("message"), message_handler)
        self.assertIs(registry.resolve("follow"), follow_handler)
        self.assertIsNone(registry.resolve("unknown"))

    # テストケース: 同じ event type の handler を二件登録する
    # 期待値: fan-out を作らず起動時の重複登録を拒否する
    def test_rejects_duplicate_event_type_registration(self) -> None:
        with self.assertRaises(ValueError):
            StaticHandlerRegistry(
                (("message", _StubHandler()), ("message", _StubHandler()))
            )

    # テストケース: registry 構築後に登録元の list を変更する
    # 期待値: request 処理で使う解決結果は起動時 snapshot から変化しない
    def test_registration_snapshot_is_immutable(self) -> None:
        handler = _StubHandler()
        registrations = [("message", handler)]
        registry = StaticHandlerRegistry(registrations)

        registrations.clear()

        self.assertIs(registry.resolve("message"), handler)
        self.assertFalse(hasattr(registry, "register"))
