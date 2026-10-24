"""現在閲覧全体のSQL・外部呼出・HTTP応答サイズを安全な集計値で検証する。"""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from lineaccounts.authentication import OWNER_SESSION_KEY
from lineaccounts.models import OwnerSession
from linerichmenus.current_types import CurrentMenuSucceeded
from linerichmenus.gateway import RichMenuDefaultNone
from linerichmenus.models import ManagedRichMenu, RichMenuOperation
from linerichmenus.presenters import RichMenuPresenter

from .test_current_concurrency import CurrentRealPortsFixture
from .test_current_service import NOW


class CurrentPerformanceTests(CurrentRealPortsFixture):
    def measure(self):
        with CaptureQueriesContext(connection) as captured:
            result = self.read()
        self.assertIsInstance(result, CurrentMenuSucceeded)
        queries = [
            q["sql"]
            for q in captured
            if not q["sql"].startswith(
                ("BEGIN", "COMMIT", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK")
            )
        ]
        self.assertLessEqual(len(queries), 32)
        self.assertFalse(any("linerichmenus_transition" in sql for sql in queries))
        operation_reads = [
            sql
            for sql in queries
            if sql.startswith("SELECT") and "FROM `linerichmenus_operation`" in sql
        ]
        self.assertTrue(operation_reads)
        self.assertTrue(
            all(" LIMIT " in sql or " IN (" in sql or "COUNT(" in sql for sql in operation_reads)
        )
        # SQL本文には入力値が含まれるため、証拠には件数だけを出す。
        return result, len(queries)

    # テストケース: 履歴10件/1000件・旧資源1件/50件で実portを含むcurrent全体を計測する。
    # 期待値: 制御SQLを除く32 SQL以内で件数が増えず、履歴一覧・transitionを読まない。
    def test_query_budget_is_constant_with_history_and_old_resources(self):
        counts = []
        for history_count, resource_count in ((10, 1), (1000, 50)):
            existing = RichMenuOperation.objects.count()
            RichMenuOperation.objects.bulk_create(
                [
                    RichMenuOperation(
                        operation_id=uuid4(),
                        channel_state=self.state,
                        owner_identity_public_id=self.identity.public_id,
                        provider_id=self.scope.provider_id,
                        kind="apply",
                        status="succeeded",
                        stage="verifying",
                        request_fingerprint="a" * 64,
                        expected_channel_revision=NOW,
                        result_code="succeeded",
                        accepted_at=NOW - timedelta(days=1),
                    )
                    for _ in range(history_count - existing)
                ]
            )
            existing_old = ManagedRichMenu.objects.filter(lifecycle="old").count()
            for _ in range(resource_count - existing_old):
                self.make_resource(self.origin, lifecycle="old")
            self.gateway.reset_mock()
            _, count = self.measure()
            counts.append(count)
            self.assertEqual(self.gateway.get_default.call_count, 2)
            self.assertEqual(self.gateway.download_content.call_count, 1)
            self.assertEqual(
                {call[0] for call in self.gateway.mock_calls}, {"get_default", "download_content"}
            )
        self.assertEqual(counts[0], counts[1])
        print(f"current service SQL: history 10/1000, old resources 1/50 = {counts}")

    # テストケース: 画像対象のないチャネルとinactiveをservice全体で計測する。
    # 期待値: 画像なしはdefault1回、inactiveは外部呼出0回でSQL上限を守る。
    def test_no_image_and_inactive_external_call_budgets(self):
        self.state.current_resource = None
        self.state.save()
        ManagedRichMenu.objects.filter(pk=self.resource.pk).update(
            lifecycle="released", released_at=NOW
        )
        self.gateway.get_default.side_effect = lambda *args: RichMenuDefaultNone()
        result, unset_queries = self.measure()
        self.assertEqual(result.current.kind, "unset")
        self.assertEqual([call[0] for call in self.gateway.mock_calls], ["get_default"])
        self.gateway.reset_mock()
        type(self.real_channel).objects.filter(pk=self.real_channel.pk).update(is_active=False)
        result, inactive_queries = self.measure()
        self.assertEqual(result.current.kind, "inactive")
        self.assertEqual(self.gateway.mock_calls, [])
        print(f"current service SQL: unset={unset_queries}, inactive={inactive_queries}")

    # テストケース: 最大1 MiB画像と設定・state・回復情報を含むHTTP JSONを計測する。
    # 期待値: Base64は1,398,104文字で一箇所だけに存在し、集計値だけを出力する。
    def test_maximum_image_response_size(self):
        RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="cleanup_required")
        self.state.blocking_operation = self.origin
        self.state.save()
        self.make_resource(self.origin, lifecycle="old", replacement_operation=self.origin)
        binary = b"x" * 1_048_576
        self.image_result = replace(self.image_result, byte_size=len(binary), binary=binary)
        result, _ = self.measure()
        body = RichMenuPresenter().current(result)
        encoded = body["current"]["image"]["base64"]
        self.assertEqual(len(encoded), 1_398_104)
        response = JSONRenderer().render(body)
        self.assertEqual(response.count(encoded.encode()), 1)
        self.assertIn("rich", body)
        self.assertIn("configuration", body["current"])
        self.assertTrue(body["recoveryTargets"])
        self.assertTrue(body["rich"]["cleanupResources"])
        self.assertLess(len(response), 1_450_000)
        self.assertNotIn(b"test-token", response)
        print(f"current maximum image: base64_chars={len(encoded)}, response_bytes={len(response)}")

    # テストケース: 実Django session認証を通してcurrent HTTP endpointを取得する。
    # 期待値: service全体のSQLを引かずに32以内で測り、HTTP認証層の追加SQLを別集計する。
    def test_http_authentication_queries_are_measured_separately(self):
        OwnerSession.objects.filter(public_id=self.session.public_id).update(
            expires_at=timezone.now() + timedelta(days=1)
        )
        client = APIClient()
        session = client.session
        session[OWNER_SESSION_KEY] = str(self.session.public_id)
        session.save()
        service_queries = []
        real_get = self.service.get_current

        def measured(*args):
            with CaptureQueriesContext(connection) as queries:
                value = real_get(*args)
            service_queries.extend(
                q["sql"]
                for q in queries
                if not q["sql"].startswith(
                    ("BEGIN", "COMMIT", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK")
                )
            )
            return value

        with patch(
            "linerichmenus.views.build_current_menu_service",
            return_value=SimpleNamespace(get_current=measured),
        ):
            with CaptureQueriesContext(connection) as total:
                response = client.get(
                    reverse("linerichmenus:current", args=[self.scope.channel_public_id]),
                    {"channelRevision": NOW.isoformat()},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        total_queries = [
            q
            for q in total
            if not q["sql"].startswith(
                ("BEGIN", "COMMIT", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK")
            )
        ]
        self.assertLessEqual(len(service_queries), 32)
        overhead = len(total_queries) - len(service_queries)
        self.assertGreater(overhead, 0)
        print(
            f"current SQL: service={len(service_queries)}, HTTP authentication/session overhead={overhead}"
        )
