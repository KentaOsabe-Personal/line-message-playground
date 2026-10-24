import ast
import json
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from django.test import SimpleTestCase

from linerichmenus import current_types
from linerichmenus.current_types import (
    ConfigurationAvailable,
    ConfigurationUnavailable,
    CurrentContent,
    CurrentImageAvailable,
    CurrentImageUnavailable,
    CurrentMenuSucceeded,
    CurrentOperationSnapshot,
    CurrentReadConflict,
    CurrentReadMatched,
    CurrentReadSnapshot,
    CurrentResourceSnapshot,
    CurrentStateSnapshot,
    RecoveryTarget,
)
from linerichmenus.repository import OwnerChannelScope
from linerichmenus.types import (
    ChannelStateView,
    HistorySummary,
    NormalizedTemplate,
    OperationKind,
    OperationStage,
    OperationStatus,
    ResourceLifecycle,
    SafeResultCode,
    TemplateFieldValue,
    TemplateReference,
)

NOW = datetime(2026, 10, 18, tzinfo=UTC)
CHANNEL = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RESOURCE = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OPERATION = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def read_snapshot():
    return CurrentReadSnapshot(
        state=CurrentStateSnapshot(RESOURCE, None, None),
        resources=(
            CurrentResourceSnapshot(
                RESOURCE,
                CHANNEL,
                OPERATION,
                None,
                "richmenu-fixture",
                ResourceLifecycle.APPLIED,
                "a" * 64,
                NOW,
            ),
        ),
        operations=(
            CurrentOperationSnapshot(
                OPERATION,
                CHANNEL,
                CHANNEL,
                "fixture-provider",
                OperationKind.APPLY,
                OperationStatus.SUCCEEDED,
                OperationStage.VERIFYING,
                None,
                None,
                "b" * 64,
                NOW,
            ),
        ),
        deactivation=None,
        configuration=ConfigurationUnavailable("mapping_missing"),
        release_target_id=RESOURCE,
        recovery_targets=(),
    )


class CurrentReadTypeTests(SimpleTestCase):
    # テストケース: 比較snapshotを保持して資源・設定・操作と行不存在を変更する。
    # 期待値: 各比較値を区別し、観測日時を比較値へ混入させず不変な値を維持する。
    def test_snapshot_tracks_changes_and_absence(self):
        snapshot = read_snapshot()
        self.assertEqual(snapshot, read_snapshot())
        for altered in (
            replace(snapshot, state=None),
            replace(snapshot, resources=()),
            replace(snapshot, operations=()),
            replace(
                snapshot,
                resources=(replace(snapshot.resources[0], updated_at=NOW + timedelta(seconds=1)),),
            ),
            replace(
                snapshot,
                operations=(replace(snapshot.operations[0], configuration_digest="c" * 64),),
            ),
            replace(
                snapshot,
                operations=(replace(snapshot.operations[0], status=OperationStatus.UNKNOWN),),
            ),
            replace(snapshot, release_target_id=None),
        ):
            self.assertNotEqual(snapshot, altered)
        with self.assertRaises(FrozenInstanceError):
            snapshot.state = None
        with self.assertRaises(ValueError):
            replace(snapshot, resources=list(snapshot.resources))

    # テストケース: URLと画像のcanaryを含む値を結果・snapshotへ合成する。
    # 期待値: 元値を変更せず保持し、repr・strへ内容を露出しない。
    def test_content_is_preserved_and_redacted(self):
        uri = "https://example.com/path?canary=full-url-value#anchor"
        configuration = ConfigurationAvailable(
            NormalizedTemplate(
                TemplateReference("jp-link-one", 1),
                (TemplateFieldValue("公式サイト", uri),),
            ),
            "editable",
        )
        image = CurrentImageAvailable("image/png", 2500, 843, "a" * 64, b"image-canary")
        content = CurrentContent("managed", RESOURCE, image, configuration)
        result = CurrentMenuSucceeded(
            CHANNEL,
            NOW,
            NOW,
            ChannelStateView(
                CHANNEL, None, None, None, (), None, HistorySummary(0, None, None), ()
            ),
            None,
            content,
            (),
        )
        self.assertEqual(configuration.template.fields[0].uri, uri)
        self.assertEqual(image.binary, b"image-canary")
        for value in (
            configuration,
            image,
            content,
            result,
            replace(read_snapshot(), configuration=configuration),
        ):
            self.assertNotIn("full-url-value", repr(value))
            self.assertNotIn("image-canary", str(value))
        self.assertFalse(hasattr(result, "confirmation_token"))
        self.assertFalse(hasattr(result, "expires_at"))

    # テストケース: 閲覧tag・失敗理由・UUID・日時・画像metadataに不正値を渡す。
    # 期待値: 不整合な値を安全な例外で拒否し、外部入力を例外へ含めない。
    def test_closed_variants_and_invalid_values(self):
        for kind in ("unset", "external", "attention", "inactive"):
            self.assertEqual(CurrentContent(kind).kind, kind)
            with self.assertRaises(ValueError):
                CurrentContent(kind, RESOURCE)
        for reason in ("missing", "invalid", "fetch_failed", "rate_limited"):
            self.assertEqual(CurrentImageUnavailable(reason).reason, reason)
        for reason in ("mapping_missing", "configuration_invalid"):
            self.assertEqual(ConfigurationUnavailable(reason).reason, reason)
        for factory in (
            lambda: CurrentContent("managed"),
            lambda: CurrentContent("unknown"),
            lambda: CurrentImageUnavailable("raw-error-canary"),
            lambda: CurrentImageAvailable("image/svg+xml", 2500, 843, "a" * 64, b"x"),
            lambda: CurrentImageAvailable(
                "image/png", 2500, 843, "a" * 64, b"x" * (1024 * 1024 + 1)
            ),
            lambda: CurrentImageAvailable("image/png", 2500, 843, "bad", b"x"),
            lambda: replace(read_snapshot().operations[0], updated_at=NOW.replace(tzinfo=None)),
            lambda: RecoveryTarget("cleanup", "not-uuid", RESOURCE),
        ):
            with self.assertRaises(ValueError) as error:
                factory()
            self.assertNotIn("raw-error-canary", str(error.exception))
        self.assertEqual(CurrentReadConflict().code, SafeResultCode.CURRENT_CHANGED)
        self.assertEqual(CurrentReadMatched(read_snapshot()).snapshot, read_snapshot())

    # テストケース: 公開repository/deactivation portに既存scopeとfakeを渡す。
    # 期待値: モデルやserviceを値moduleからimportせず同一契約で合成できる。
    def test_ports_compose_without_reverse_runtime_imports(self):
        scope = OwnerChannelScope(CHANNEL, "fixture-provider", CHANNEL)

        class FakeRepository:
            def capture_current(self, scope):
                return read_snapshot()

            def lock_current_unchanged(self, scope, expected):
                return CurrentReadMatched(expected)

        repository: current_types.CurrentReadRepository = FakeRepository()
        self.assertEqual(repository.capture_current(scope), read_snapshot())
        self.assertEqual(
            repository.lock_current_unchanged(scope, read_snapshot()).snapshot, read_snapshot()
        )
        tree = ast.parse(Path(current_types.__file__).read_text())
        runtime_imports = [node.module for node in tree.body if isinstance(node, ast.ImportFrom)]
        self.assertNotIn("repository", runtime_imports)
        self.assertNotIn("services", runtime_imports)
        self.assertNotIn("models", runtime_imports)

    # テストケース: Frontendと共通のHTTP fixtureをDjangoから読み込む。
    # 期待値: 全表示tag・部分失敗・回復・停止を既存state schemaと併用できる。
    def test_shared_http_fixtures(self):
        fixture_path = Path("/test-fixtures/rich-menu-current.json")
        if not fixture_path.exists():
            fixture_path = (
                Path(__file__).resolve().parents[3]
                / "frontend/test/fixtures/rich-menu-current.json"
            )
        fixtures = json.loads(fixture_path.read_text())
        self.assertEqual(
            {item["current"]["kind"] for item in fixtures.values()},
            {"managed", "unset", "external", "attention", "inactive"},
        )
        for item in fixtures.values():
            self.assertEqual(UUID(item["channelId"]), CHANNEL)
            self.assertIn("currentResource", item["rich"])
            self.assertNotIn("confirmationToken", item)
            self.assertNotIn("expiresAt", item)
            self.assertEqual(item["checkedAt"] is None, item["current"]["kind"] == "inactive")

    # テストケース: 現在閲覧fixtureの元PNGを既存fake LINE gatewayで観測する。
    # 期待値: compact/largeの寸法とcanonical digestが一致し実LINEを呼ばない。
    def test_fixture_images_work_with_existing_fake_gateway(self):
        import base64
        from io import BytesIO

        from linechannels.types import AccessToken
        from linerichmenus.gateway import DefaultRichMenuGateway, RichMenuGatewayContext

        from .test_gateway import FakeFactory

        fixtures = json.loads(Path("/test-fixtures/rich-menu-current.json").read_text())
        for name in ("managed", "managedLarge"):
            image = fixtures[name]["current"]["image"]
            factory = FakeFactory()
            factory.blob.responses["get_rich_menu_image"] = BytesIO(
                base64.b64decode(image["base64"])
            )
            observed = DefaultRichMenuGateway(client_factory=factory).download(
                RichMenuGatewayContext(CHANNEL, NOW, AccessToken("fixture-only-access-token")),
                "richmenu-fixture",
            )
            self.assertEqual(observed.width, image["width"])
            self.assertEqual(observed.height, image["height"])
            self.assertEqual(observed.pixel_digest, image["digest"])
            self.assertEqual(factory.close_calls, 1)

    # テストケース: 停止intentの不存在・作成・status・更新日時を比較する。
    # 期待値: 公開値だけで全差分を検出し資格情報をsnapshotへ保持しない。
    def test_deactivation_snapshot_tracks_public_intent(self):
        from linechannels.admin_lifecycle_types import DeactivationView
        from linerichmenus.types import OwnerChannelScope as SharedScope

        self.assertIs(OwnerChannelScope, SharedScope)
        snapshot = read_snapshot()
        intent = DeactivationView(
            CHANNEL,
            True,
            NOW,
            OPERATION,
            NOW,
            "checking",
            None,
            None,
            None,
            NOW,
            NOW,
            None,
        )
        with_intent = replace(snapshot, deactivation=intent)
        self.assertNotEqual(snapshot, with_intent)
        for changed in (
            replace(intent, status="completed"),
            replace(intent, updated_at=NOW + timedelta(seconds=1)),
        ):
            self.assertNotEqual(with_intent, replace(snapshot, deactivation=changed))
        self.assertNotIn("access_token", {field.name for field in fields(snapshot)})
