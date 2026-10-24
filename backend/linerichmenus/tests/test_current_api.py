from base64 import b64decode
from unittest.mock import patch
from uuid import uuid4

from django.urls import resolve, reverse
from rest_framework.test import APIClient

from lineaccounts.admin_authorization import OwnerFenceFailed
from lineaccounts.authentication import OwnerPrincipal
from linechannels.admin_lifecycle_types import DeactivationView
from linechannels.admin_presenters import AdminPresenter
from linerichmenus.container import build_current_menu_service
from linerichmenus.gateway import ImageAbsent, RichMenuDefaultNone, RichMenuDefaultPresent
from linerichmenus.services import ServiceFailed
from linerichmenus.types import SafeResultCode
from linerichmenus.views import ChannelCurrentAPIView

from .test_current_service import NOW, CurrentServiceFixture


class CurrentAPITests(CurrentServiceFixture):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(
            OwnerPrincipal(self.owner.owner_session_id, self.owner.identity_public_id, "active")
        )
        self.url = reverse("linerichmenus:current", args=[self.scope.channel_public_id])
        self.builder = patch(
            "linerichmenus.views.build_current_menu_service", return_value=self.service
        )
        self.builder.start()
        self.addCleanup(self.builder.stop)

    def get(self, query=None):
        return self.client.get(
            self.url, {"channelRevision": NOW.isoformat()} if query is None else query
        )

    def assert_no_store(self, response, status):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")

    # テストケース: 認証済みownerが現在資源を単発GETする。
    # 期待値: allowlistのJSON、元画像bytes、完全URL、観測時刻だけを返す。
    def test_managed_current_contract(self):
        response = self.get()
        self.assert_no_store(response, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "channelId",
                "channelRevision",
                "checkedAt",
                "rich",
                "deactivation",
                "current",
                "recoveryTargets",
            },
        )
        self.assertEqual(body["channelId"], str(self.scope.channel_public_id))
        self.assertEqual(body["channelRevision"], NOW.isoformat())
        self.assertEqual(body["checkedAt"], NOW.isoformat())
        self.assertEqual(body["current"]["resourceId"], str(self.resource.public_id))
        self.assertEqual(
            body["current"]["image"],
            {
                "status": "available",
                "contentType": "image/png",
                "width": 800,
                "height": 400,
                "digest": "b" * 64,
                "base64": "cG5n",
            },
        )
        self.assertEqual(b64decode(body["current"]["image"]["base64"]), b"png")
        self.assertEqual(body["current"]["configuration"]["fields"], self.configuration()["fields"])
        self.assertEqual(body["rich"]["channelId"], body["channelId"])
        self.assertEqual(self.gateway.get_default.call_count, 2)
        self.assertEqual(self.gateway.download_content.call_count, 1)
        self.assertNotIn("confirmationToken", response.content.decode())
        self.assertNotIn("expiresAt", response.content.decode())

    # テストケース: 画像だけが欠落している管理対象を閲覧する。
    # 期待値: 200でmanaged・設定・許可操作を保持し、安全な部分失敗を返す。
    def test_image_partial_failure(self):
        self.image_result = ImageAbsent()
        response = self.get()
        self.assert_no_store(response, 200)
        body = response.json()
        self.assertEqual(body["current"]["kind"], "managed")
        self.assertEqual(body["current"]["image"], {"status": "unavailable", "reason": "missing"})
        self.assertEqual(body["current"]["configuration"]["status"], "available")
        self.assertIn("release", body["rich"]["effectiveActions"])

    # テストケース: 停止中のチャネルと保存済み停止情報を閲覧する。
    # 期待値: checkedAtはnullで外部照会せず、停止情報は既存presenterと一致する。
    def test_inactive_deactivation_presenter(self):
        self.metadata.is_active = False
        view = DeactivationView(
            self.scope.channel_public_id,
            False,
            NOW,
            uuid4(),
            NOW,
            "completed",
            None,
            None,
            None,
            NOW,
            NOW,
            NOW,
        )
        self.service._current_repository._deactivation.get_for_owner = lambda *args: view
        response = self.get()
        self.assert_no_store(response, 200)
        body = response.json()
        self.assertEqual(body["checkedAt"], None)
        self.assertEqual(body["current"], {"kind": "inactive"})
        self.assertEqual(body["deactivation"], AdminPresenter().deactivation(view))
        self.assertEqual(self.gateway.mock_calls, [])
        self.channel.snapshot_exact.assert_not_called()

    # テストケース: 必須revision欠落・naive・不正日付・未知key・複数値を送る。
    # 期待値: 安全な400とno-storeを返し外部通信しない。
    def test_invalid_query(self):
        for query in (
            {},
            {"channelRevision": "2026-10-19T00:00:00"},
            {"channelRevision": "2026-99-99T00:00:00Z"},
            {"channelRevision": "private-canary"},
            {"channelRevision": NOW.isoformat(), "private-canary": "secret"},
            {"channelRevision": [NOW.isoformat(), NOW.isoformat()]},
        ):
            with self.subTest(query=query):
                response = self.get(query)
                self.assert_no_store(response, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_input")
                self.assertNotIn("private-canary", response.content.decode())
        self.assertEqual(self.gateway.mock_calls, [])

    # テストケース: serviceが認証・認可・不存在・競合・利用不能を返す。
    # 期待値: 公開契約のstatus/bodyとno-storeを返す。
    def test_safe_error_statuses(self):
        cases = (
            (SafeResultCode.INVALID_INPUT, 400),
            (SafeResultCode.AUTHENTICATION_REQUIRED, 401),
            (SafeResultCode.OWNER_OPERATION_BLOCKED, 403),
            (SafeResultCode.CHANNEL_UNAVAILABLE, 404),
            (SafeResultCode.STALE_CHANNEL, 409),
            (SafeResultCode.CURRENT_CHANGED, 409),
            (SafeResultCode.OBSERVATION_UNKNOWN, 503),
            (SafeResultCode.INTEGRATION_NOT_READY, 503),
            (SafeResultCode.STORAGE_UNAVAILABLE, 503),
        )
        for code, status in cases:
            with (
                self.subTest(code=code),
                patch.object(self.service, "get_current", return_value=ServiceFailed(code)),
            ):
                response = self.get()
                self.assert_no_store(response, status)
                self.assertEqual(
                    response.json(), {"error": {"code": code.value, "nextAllowedActions": []}}
                )

    # テストケース: sessionなしまたはinactive ownerが閲覧する。
    # 期待値: 認証層が401/403を返し外部通信せずno-storeを付ける。
    def test_authentication_and_permission(self):
        self.client.force_authenticate(user=None)
        self.assert_no_store(self.get(), 401)
        self.client.force_authenticate(OwnerPrincipal(uuid4(), uuid4(), "local_deletion_pending"))
        self.assert_no_store(self.get(), 403)
        self.assertEqual(self.gateway.mock_calls, [])

    # テストケース: 画像取得中に資源変更またはowner認可失効が起きる。
    # 期待値: 409/403と安全なエラーだけを返し画像・設定を採用しない。
    def test_final_fence_conflict_and_revocation(self):
        def mutate():
            type(self.resource).objects.filter(pk=self.resource.pk).update(image_digest="c" * 64)

        self.after_image = mutate
        self.assert_no_store(self.get(), 409)
        type(self.resource).objects.filter(pk=self.resource.pk).update(image_digest="b" * 64)
        self.after_image = lambda: setattr(
            self.fence.lock_active, "return_value", OwnerFenceFailed("owner_operation_blocked")
        )
        self.assert_no_store(self.get(), 403)

    # テストケース: 画像または合成処理が秘密canaryを含む例外を送出する。
    # 期待値: 画像部分失敗または503へ縮退し、応答・通常ログへ例外を出さない。
    def test_exception_canary_not_in_response_or_logs(self):
        canary = "private-token-image-https://example.invalid/private?secret=canary"
        with self.assertNoLogs(level="WARNING"):
            self.gateway.download_content.side_effect = RuntimeError(canary)
            response = self.get()
            self.assert_no_store(response, 200)
            self.assertNotIn(canary, response.content.decode())
        with self.assertLogs("django.request", level="ERROR") as captured:
            with patch(
                "linerichmenus.views.build_current_menu_service", side_effect=RuntimeError(canary)
            ):
                response = self.get()
                self.assert_no_store(response, 503)
                self.assertNotIn(canary, response.content.decode())
        self.assertNotIn(canary, " ".join(captured.output))

    # テストケース: currentなしの未設定・外部既定と現在資源の外部変更を閲覧する。
    # 期待値: タグだけを返し画像・設定・外部内容を返さない。
    def test_nonmanaged_tags(self):
        for current, observation, kind in (
            (True, RichMenuDefaultPresent("external"), "attention"),
            (False, RichMenuDefaultNone(), "unset"),
            (False, RichMenuDefaultPresent("external"), "external"),
        ):
            self.state.current_resource = self.resource if current else None
            self.state.save()
            if not current:
                type(self.resource).objects.filter(pk=self.resource.pk).update(
                    lifecycle="released", released_at=NOW
                )
            self.gateway.get_default.side_effect = lambda *args: observation
            response = self.get()
            self.assert_no_store(response, 200)
            self.assertEqual(response.json()["current"], {"kind": kind})
        self.gateway.download_content.assert_not_called()

    # テストケース: containerが実adapterを合成し新URLを解決する。
    # 期待値: 公開portを注入した閲覧serviceを構築し、旧state URLも維持する。
    def test_composition_and_additive_routes(self):
        service = build_current_menu_service()
        self.assertEqual(type(service).__name__, "CurrentMenuService")
        self.assertEqual(
            type(service._current_repository._deactivation).__name__,
            "DjangoChannelDeactivationRepository",
        )
        self.assertIs(service._gateway, service._reconciler._gateway)
        self.assertIs(resolve(self.url).func.view_class, ChannelCurrentAPIView)
        self.assertEqual(
            resolve(reverse("linerichmenus:state", args=[self.scope.channel_public_id])).url_name,
            "state",
        )


class CurrentRealSessionTests(CurrentServiceFixture):
    def setUp(self):
        super().setUp()
        from datetime import timedelta

        from django.db import transaction
        from django.utils import timezone

        from lineaccounts.authentication import OWNER_SESSION_KEY
        from lineaccounts.gateway import VerifiedLineIdentity
        from lineaccounts.models import OwnerAccount
        from lineaccounts.repositories import DjangoAccountRepository
        from lineaccounts.types import LineSubject
        from linechannels.container import build_line_channel_service
        from linechannels.types import RegisterLineChannel
        from linechannels.validators import build_credential_pair

        OwnerAccount.objects.get_or_create(slot=1)
        repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = repository.lock_owner_account()
            identity = repository.upsert_identity(
                VerifiedLineIdentity(
                    self.scope.provider_id,
                    LineSubject("U" + uuid4().hex),
                    "閲覧owner",
                )
            )
            owner = repository.bind_owner_identity(owner, identity.public_id)
            session = repository.create_owner_session(owner, timezone.now() + timedelta(hours=1))
        channel = (
            build_line_channel_service()
            .register(
                RegisterLineChannel(
                    "1234567890",
                    "U" + uuid4().hex,
                    "閲覧チャネル",
                    build_credential_pair("fixture-access-token", "fixture-channel-secret"),
                    True,
                    self.scope.provider_id,
                )
            )
            .channel
        )
        self.real_identity_id = identity.public_id
        self.channel_id = channel.public_id
        self.revision = channel.updated_at
        self.client = APIClient(enforce_csrf_checks=True)
        browser_session = self.client.session
        browser_session[OWNER_SESSION_KEY] = str(session.public_id)
        browser_session.save()
        self.url = reverse("linerichmenus:current", args=[self.channel_id])

    # テストケース: 実owner sessionとcontainerで現在未設定のチャネルを閲覧する。
    # 期待値: 実認可・資格情報・保存情報を合成し、旧state endpointも利用できる。
    def test_real_composition_and_old_state(self):
        from unittest.mock import Mock

        gateway = Mock()
        gateway.get_default.return_value = RichMenuDefaultNone()
        with patch("linerichmenus.container.DefaultRichMenuGateway", return_value=gateway):
            response = self.client.get(self.url, {"channelRevision": self.revision.isoformat()})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["current"], {"kind": "unset"})
            self.assertEqual(response["Cache-Control"], "no-store")
            old = self.client.get(reverse("linerichmenus:state", args=[self.channel_id]))
            self.assertEqual(old.status_code, 200, old.content)
        gateway.download_content.assert_not_called()

    # テストケース: 実owner sessionから異なるprovider・不存在・stale revisionを参照する。
    # 期待値: 404/409を返し外部照会へ進めない。
    def test_real_provider_and_revision_fences(self):
        from linechannels.models import LineChannel

        with patch("linerichmenus.container.DefaultRichMenuGateway") as gateway:
            stale = self.client.get(self.url, {"channelRevision": NOW.isoformat()})
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["error"]["code"], "stale_channel")
            LineChannel.objects.filter(public_id=self.channel_id).update(
                provider_id="other-provider"
            )
            hidden = self.client.get(self.url, {"channelRevision": self.revision.isoformat()})
            self.assertEqual(hidden.status_code, 404)
            missing = self.client.get(
                reverse("linerichmenus:current", args=[uuid4()]),
                {"channelRevision": self.revision.isoformat()},
            )
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(hidden["Cache-Control"], "no-store")
            gateway.return_value.get_default.assert_not_called()

    def seed_current_for_release(self):
        from dataclasses import replace

        from linerichmenus.models import RichMenuChannelState

        self.scope = replace(
            self.scope,
            owner_identity_public_id=self.real_identity_id,
            channel_public_id=self.channel_id,
        )
        self.state = RichMenuChannelState.objects.create(channel_public_id=self.channel_id)
        self.origin = self.operation(
            configuration_snapshot=self.configuration(), expected_channel_revision=self.revision
        )
        self.resource = self.make_resource(self.origin, lifecycle="applied")
        self.state.current_resource = self.resource
        self.state.save()

    def post_release(self, target=None, revision=None):
        from django.conf import settings
        from django.middleware.csrf import get_token
        from rest_framework.test import APIRequestFactory

        seed = APIRequestFactory().get("/api/account/session/")
        token = get_token(seed)
        self.client.cookies[settings.CSRF_COOKIE_NAME] = seed.META["CSRF_COOKIE"]
        return self.client.post(
            reverse("linerichmenus:operations", args=[self.channel_id]),
            {
                "kind": "release",
                "operationId": str(uuid4()),
                "channelRevision": (revision or self.revision).isoformat(),
                "targetResourceId": str(target or self.resource.pk),
            },
            format="json",
            HTTP_ORIGIN=settings.CSRF_TRUSTED_ORIGINS[0],
            HTTP_X_CSRFTOKEN=token,
        )

    def release_settings(self):
        return self.settings(
            LINE_RICH_MENU_MUTATION_MODE="enabled",
            LINE_RICH_MENU_REFERENCE_PROBE_INTEGRATED=True,
            LINE_RICH_MENU_HISTORY_PURGE_INTEGRATED=True,
            LINE_RICH_MENU_DEACTIVATION_LIFECYCLE_INTEGRATED=True,
            LINE_RICH_MENU_INTEGRATION_MARKER="line-rich-menu-admin-lifecycle-v1",
        )

    # テストケース: 実sessionでcurrent候補を読み、既存release APIを確定し再取得する。
    # 期待値: 現在管理資源だけ追跡終了し、履歴を保持してLINE変更・削除・自動再取込を行わない。
    def test_current_candidate_connects_to_existing_release_and_retains_history(self):
        from unittest.mock import Mock

        from linerichmenus.gateway import DownloadedImage
        from linerichmenus.models import RichMenuOperation

        self.seed_current_for_release()
        gateway = Mock()
        gateway.get_default.return_value = RichMenuDefaultPresent(self.resource.line_rich_menu_id)
        gateway.download_content.return_value = DownloadedImage(
            "image/png", 800, 400, "b" * 64, 3, binary=b"png"
        )
        with (
            self.release_settings(),
            patch("linerichmenus.container.DefaultRichMenuGateway", return_value=gateway),
        ):
            current = self.client.get(self.url, {"channelRevision": self.revision.isoformat()})
            self.assertEqual(current.status_code, 200, current.content)
            self.assertIn("release", current.json()["rich"]["effectiveActions"])
            target = current.json()["rich"]["currentResource"]["resourceId"]
            self.assertEqual(target, str(self.resource.pk))
            gateway.reset_mock()
            response = self.post_release()
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["status"], "succeeded")
            self.assertEqual(response.json()["targetResourceId"], target)
            self.assertEqual(gateway.mock_calls, [])
            self.resource.refresh_from_db()
            self.state.refresh_from_db()
            self.assertEqual(self.resource.lifecycle, "released")
            self.assertIsNone(self.state.current_resource_id)
            self.assertEqual(RichMenuOperation.objects.filter(channel_state=self.state).count(), 2)
            for _ in range(2):
                refreshed = self.client.get(
                    self.url, {"channelRevision": self.revision.isoformat()}
                )
                self.assertEqual(refreshed.status_code, 200, refreshed.content)
                self.assertEqual(refreshed.json()["current"], {"kind": "external"})
                self.assertIsNone(refreshed.json()["rich"]["currentResource"])
                self.assertEqual(refreshed.json()["rich"]["cleanupResources"], [])
                self.assertNotIn("release", refreshed.json()["rich"]["effectiveActions"])
            self.assertEqual({call[0] for call in gateway.mock_calls}, {"get_default"})
            self.resource.refresh_from_db()
            self.assertEqual(self.resource.lifecycle, "released")

    # テストケース: 閲覧後にrevision・対象current・操作参照・停止intentを変更する。
    # 期待値: 既存mutationが競合を拒否し、LINE変更も追跡終了も発生しない。
    def test_release_refuses_changed_preconditions_after_current_read(self):
        from unittest.mock import Mock

        from linechannels.models import ChannelDeactivationState, LineChannel
        from linerichmenus.models import RichMenuOperation

        self.seed_current_for_release()
        gateway = Mock()
        gateway.get_default.return_value = RichMenuDefaultPresent("external-default")
        with (
            self.release_settings(),
            patch("linerichmenus.container.DefaultRichMenuGateway", return_value=gateway),
        ):
            current = self.client.get(self.url, {"channelRevision": self.revision.isoformat()})
            self.assertIn("release", current.json()["rich"]["effectiveActions"])
            gateway.reset_mock()
            stale = self.post_release(revision=NOW)
            self.assertEqual(stale.status_code, 409, stale.content)
            foreign = self.post_release(target=uuid4())
            self.assertNotEqual(foreign.status_code, 200)
            self.state.current_resource = None
            self.state.save()
            changed = self.post_release()
            self.assertNotEqual(changed.status_code, 200)
            self.state.current_resource = self.resource
            self.state.active_operation = self.origin
            self.state.save()
            RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="processing")
            active = self.post_release()
            self.assertEqual(active.status_code, 409, active.content)
            self.state.active_operation = None
            self.state.save()
            RichMenuOperation.objects.filter(pk=self.origin.pk).update(status="succeeded")
            ChannelDeactivationState.objects.create(
                line_channel=LineChannel.objects.get(public_id=self.channel_id),
                owner_identity_public_id=self.real_identity_id,
                provider_id=self.scope.provider_id,
                expected_channel_revision=self.revision,
                operation_id=uuid4(),
                status="checking",
            )
            pending = self.post_release()
            self.assertEqual(pending.status_code, 409, pending.content)
            self.assertEqual(gateway.mock_calls, [])
            self.resource.refresh_from_db()
            self.assertEqual(self.resource.lifecycle, "applied")
