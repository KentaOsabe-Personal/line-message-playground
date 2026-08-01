from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lineaccounts.authentication import OWNER_SESSION_KEY
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import DeliveryRecipient, LineIdentity
from lineaccounts.repositories import DjangoAccountRepository
from lineaccounts.types import LineSubject
from linechannels.container import build_channel_admin_service
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.types import ChannelMutationFailed


class AdminAPIIntegrationTests(TestCase):
    def setUp(self):
        self.origin = "https://test.example.ngrok.app"
        self.provider_id = "0012345678"
        repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = repository.lock_owner_account()
            self.identity = repository.upsert_identity(
                VerifiedLineIdentity(
                    provider_id=self.provider_id,
                    subject=LineSubject(f"U{uuid4().hex}"),
                    display_name="Owner",
                )
            )
            owner = repository.bind_owner_identity(owner, self.identity.public_id)
            self.owner_session = repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )

    def owner_client(self):
        client = APIClient(enforce_csrf_checks=True)
        session = client.session
        session[OWNER_SESSION_KEY] = str(self.owner_session.public_id)
        session.save()
        bootstrap = client.get("/api/account/session/")
        return client, bootstrap.cookies["csrftoken"].value

    def unsafe(self, client, method, path, body, csrf):
        return getattr(client, method)(
            path,
            body,
            format="json",
            HTTP_ORIGIN=self.origin,
            HTTP_X_CSRFTOKEN=csrf,
        )

    def create_body(self):
        return {
            "label": "通知チャネル",
            "messagingApiChannelId": "123456789012345",
            "botUserId": f"U{'a' * 32}",
            "providerId": self.provider_id,
            "accessToken": "create-access-token-canary",
            "channelSecret": "create-channel-secret-canary",
            "active": True,
        }

    # テストケース: unsafe endpointへCSRF欠落・不正Origin・秘密を含む不正shapeを送る
    # 期待値: serializer/service mutation前に拒否され、DBと応答へ秘密を残さない
    def test_unsafe_requests_fail_before_database_mutation_without_secret_exposure(self):
        client, csrf = self.owner_client()
        body = self.create_body()

        missing_csrf = client.post(
            "/api/line/channels/",
            body,
            format="json",
            HTTP_ORIGIN=self.origin,
        )
        bad_origin = client.post(
            "/api/line/channels/",
            body,
            format="json",
            HTTP_ORIGIN="https://evil.example",
            HTTP_X_CSRFTOKEN=csrf,
        )
        malformed = self.unsafe(
            client,
            "post",
            "/api/line/channels/",
            {**body, "unexpectedSecret": "shape-secret-canary"},
            csrf,
        )

        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(bad_origin.status_code, 403)
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(LineChannel.objects.count(), 0)
        rendered = " ".join(str(response.json()) for response in (missing_csrf, bad_origin, malformed))
        for canary in (
            body["accessToken"],
            body["channelSecret"],
            "shape-secret-canary",
        ):
            self.assertNotIn(canary, rendered)

    # テストケース: ownerが公開HTTPだけで登録・metadata更新・資格情報置換・無効化・削除する
    # 期待値: safe DTOと実DBが一致し、秘密値は応答せず、各mutationは原子的に完了する
    def test_owner_executes_complete_channel_lifecycle_through_public_http(self):
        client, csrf = self.owner_client()
        created = self.unsafe(client, "post", "/api/line/channels/", self.create_body(), csrf)
        self.assertEqual(created.status_code, 201)
        channel_id = created.json()["channelId"]
        first_revision = created.json()["updatedAt"]
        credential = LineChannelCredential.objects.get(line_channel__public_id=channel_id)
        original_ciphertexts = (
            bytes(credential.access_token_ciphertext),
            bytes(credential.channel_secret_ciphertext),
        )

        metadata = self.unsafe(
            client,
            "patch",
            f"/api/line/channels/{channel_id}/",
            {"expectedUpdatedAt": first_revision, "label": "更新後チャネル"},
            csrf,
        )
        self.assertEqual(metadata.status_code, 200)
        credential.refresh_from_db()
        self.assertEqual(
            original_ciphertexts,
            (
                bytes(credential.access_token_ciphertext),
                bytes(credential.channel_secret_ciphertext),
            ),
        )

        replaced = self.unsafe(
            client,
            "patch",
            f"/api/line/channels/{channel_id}/",
            {
                "expectedUpdatedAt": metadata.json()["updatedAt"],
                "accessToken": "replacement-access-token-canary",
                "channelSecret": "replacement-channel-secret-canary",
            },
            csrf,
        )
        self.assertEqual(replaced.status_code, 200)
        credential.refresh_from_db()
        self.assertNotEqual(original_ciphertexts[0], bytes(credential.access_token_ciphertext))
        self.assertNotEqual(original_ciphertexts[1], bytes(credential.channel_secret_ciphertext))

        disabled = self.unsafe(
            client,
            "post",
            f"/api/line/channels/{channel_id}/state/",
            {"expectedUpdatedAt": replaced.json()["updatedAt"], "active": False},
            csrf,
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.json()["active"])

        deleted = self.unsafe(
            client,
            "delete",
            f"/api/line/channels/{channel_id}/",
            {"expectedUpdatedAt": disabled.json()["updatedAt"]},
            csrf,
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"channelId": channel_id, "label": "更新後チャネル", "deleted": True})
        self.assertFalse(LineChannel.objects.filter(public_id=channel_id).exists())
        self.assertFalse(LineChannelCredential.objects.filter(line_channel__public_id=channel_id).exists())

        rendered = " ".join(str(response.json()) for response in (created, metadata, replaced, disabled, deleted))
        for canary in (
            "create-access-token-canary",
            "create-channel-secret-canary",
            "replacement-access-token-canary",
            "replacement-channel-secret-canary",
        ):
            self.assertNotIn(canary, rendered)

    # テストケース: owner providerと同一、legacy、別providerのチャネルを一覧・詳細取得する
    # 期待値: 同一とlegacyだけを返し、別provider詳細は不在と同じ404へ収束する
    def test_reads_are_scoped_to_owner_provider_and_legacy_channels(self):
        same = LineChannel.objects.create(
            messaging_api_channel_id="111",
            bot_user_id=f"U{'1' * 32}",
            label="same",
            provider_id=self.provider_id,
            is_active=True,
        )
        legacy = LineChannel.objects.create(
            messaging_api_channel_id="222",
            bot_user_id=f"U{'2' * 32}",
            label="legacy",
            provider_id=None,
            is_active=False,
        )
        other = LineChannel.objects.create(
            messaging_api_channel_id="333",
            bot_user_id=f"U{'3' * 32}",
            label="other",
            provider_id="9999999999",
            is_active=True,
        )
        client, _ = self.owner_client()

        listed = client.get("/api/line/channels/")
        hidden = client.get(f"/api/line/channels/{other.public_id}/")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            {item["channelId"] for item in listed.json()["items"]},
            {str(same.public_id), str(legacy.public_id)},
        )
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.json()["error"]["code"], "channel_not_found")
        self.assertNotIn(str(other.public_id), str(listed.json()))

    # テストケース: duplicate、partial pair、stale、provider不一致を公開HTTPへ送る
    # 期待値: 固定status/codeを返し、既存channelと資格情報を一切変更しない
    def test_invalid_lifecycle_mutations_are_atomic_at_the_public_http_boundary(self):
        client, csrf = self.owner_client()
        created = self.unsafe(client, "post", "/api/line/channels/", self.create_body(), csrf)
        self.assertEqual(created.status_code, 201)
        channel_id = created.json()["channelId"]
        before = LineChannel.objects.get(public_id=channel_id)
        credential = LineChannelCredential.objects.get(line_channel=before)
        before_state = (
            before.label,
            before.updated_at,
            bytes(credential.access_token_ciphertext),
            bytes(credential.channel_secret_ciphertext),
        )

        duplicate = self.unsafe(client, "post", "/api/line/channels/", self.create_body(), csrf)
        partial = self.unsafe(
            client,
            "patch",
            f"/api/line/channels/{channel_id}/",
            {"expectedUpdatedAt": created.json()["updatedAt"], "accessToken": "partial-canary"},
            csrf,
        )
        mismatched = self.unsafe(
            client,
            "post",
            "/api/line/channels/",
            {
                **self.create_body(),
                "messagingApiChannelId": "777777",
                "botUserId": f"U{'7' * 32}",
                "providerId": "9999999999",
            },
            csrf,
        )
        stale = self.unsafe(
            client,
            "patch",
            f"/api/line/channels/{channel_id}/",
            {"expectedUpdatedAt": "2020-01-01T00:00:00Z", "label": "stale-change"},
            csrf,
        )

        self.assertEqual((duplicate.status_code, duplicate.json()["error"]["code"]), (409, "duplicate_channel"))
        self.assertEqual(partial.status_code, 400)
        self.assertEqual((mismatched.status_code, mismatched.json()["error"]["code"]), (422, "provider_mismatch"))
        self.assertEqual((stale.status_code, stale.json()["error"]["code"]), (409, "stale_channel"))
        self.assertEqual(LineChannel.objects.count(), 1)
        before.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(
            (
                before.label,
                before.updated_at,
                bytes(credential.access_token_ciphertext),
                bytes(credential.channel_secret_ciphertext),
            ),
            before_state,
        )
        self.assertNotIn("partial-canary", str(partial.json()))

    # テストケース: legacy backfill、参照中delete、storage失敗後の再取得を公開HTTPで行う
    # 期待値: backfillだけを保存し、参照中/storage失敗はrollbackして再取得で実状態へ収束する
    def test_backfill_referenced_delete_and_storage_failure_converge_to_real_database_state(self):
        legacy = LineChannel.objects.create(
            messaging_api_channel_id="444444",
            bot_user_id=f"U{'4' * 32}",
            label="legacy",
            provider_id=None,
            is_active=False,
        )
        client, csrf = self.owner_client()
        listed = client.get("/api/line/channels/")
        legacy_dto = next(item for item in listed.json()["items"] if item["channelId"] == str(legacy.public_id))
        backfilled = self.unsafe(
            client,
            "patch",
            f"/api/line/channels/{legacy.public_id}/",
            {
                "expectedUpdatedAt": legacy_dto["updatedAt"],
                "providerId": self.provider_id,
                "label": "backfilled",
            },
            csrf,
        )
        self.assertEqual(backfilled.status_code, 200)
        legacy.refresh_from_db()
        self.assertEqual((legacy.provider_id, legacy.label), (self.provider_id, "backfilled"))

        DeliveryRecipient.objects.create(
            identity=LineIdentity.objects.get(public_id=self.identity.public_id),
            line_channel=legacy,
        )
        referenced = self.unsafe(
            client,
            "delete",
            f"/api/line/channels/{legacy.public_id}/",
            {"expectedUpdatedAt": backfilled.json()["updatedAt"]},
            csrf,
        )
        self.assertEqual((referenced.status_code, referenced.json()["error"]["code"]), (409, "channel_referenced"))
        self.assertTrue(LineChannel.objects.filter(public_id=legacy.public_id).exists())

        failing_service = build_channel_admin_service()
        failing_foundation = Mock()
        failing_foundation.register.return_value = ChannelMutationFailed("storage_unavailable")
        failing_service._foundation_service = failing_foundation
        with patch("linechannels.admin_views.build_channel_admin_service", return_value=failing_service):
            failed = self.unsafe(
                client,
                "post",
                "/api/line/channels/",
                {
                    **self.create_body(),
                    "messagingApiChannelId": "555555",
                    "botUserId": f"U{'5' * 32}",
                },
                csrf,
            )
        self.assertEqual((failed.status_code, failed.json()["error"]["code"]), (503, "storage_unavailable"))
        refreshed = client.get(f"/api/line/channels/{legacy.public_id}/")
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.json()["label"], "backfilled")
        self.assertFalse(LineChannel.objects.filter(messaging_api_channel_id="555555").exists())
