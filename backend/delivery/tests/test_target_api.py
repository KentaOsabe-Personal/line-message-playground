from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from lineaccounts.authentication import OWNER_SESSION_KEY
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import DeliveryRecipient, LineIdentity
from lineaccounts.repositories import DjangoAccountRepository
from lineaccounts.types import LineSubject
from linechannels.models import LineChannel


@override_settings(
    LINE_CHANNEL_ACCESS_TOKEN="fixed-token-canary",
    LINE_USER_ID="fixed-recipient-canary",
)
class DeliveryTargetAPITests(TestCase):
    def setUp(self):
        self.provider_id = "0012345678"
        self.repository = DjangoAccountRepository()
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            identity_view = self.repository.upsert_identity(
                VerifiedLineIdentity(
                    self.provider_id,
                    LineSubject("Usubject-secret-canary"),
                    "Owner display",
                )
            )
            owner = self.repository.bind_owner_identity(
                owner, identity_view.public_id
            )
            self.owner_session = self.repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )
        self.identity = LineIdentity.objects.get(public_id=identity_view.public_id)

        self.active_channel = self._channel(
            "通知チャネル",
            active=True,
            messaging_id="channel-id-secret-canary",
            bot_user_id="Ubot-secret-canary",
        )
        self.inactive_channel = self._channel("停止チャネル", active=False)
        self.no_recipient_channel = self._channel("未登録チャネル")
        self.friend = self._recipient(
            self.active_channel,
            enabled=True,
            friendship_state=DeliveryRecipient.FriendshipState.FRIEND,
        )

    def _channel(
        self,
        label,
        *,
        active=True,
        provider_id=None,
        messaging_id=None,
        bot_user_id=None,
    ):
        return LineChannel.objects.create(
            messaging_api_channel_id=messaging_id or str(uuid4().int)[:20],
            bot_user_id=bot_user_id or f"U{uuid4().hex}",
            label=label,
            provider_id=provider_id or self.provider_id,
            is_active=active,
        )

    def _recipient(self, channel, *, enabled, friendship_state):
        return DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=channel,
            enabled=enabled,
            friendship_state=friendship_state,
        )

    def owner_client(self):
        client = APIClient(enforce_csrf_checks=True)
        session = client.session
        session[OWNER_SESSION_KEY] = str(self.owner_session.public_id)
        session.save()
        return client

    # テストケース: active ownerがchannel選択肢を取得する
    # 期待値: stable順のstrict safe DTOだけを返し、秘密値と固定env値を含めない
    def test_active_owner_lists_safe_channel_choices_in_stable_order(self):
        response = self.owner_client().get(
            "/api/deliveries/targets/channels/"
        )

        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(
            [item["channelId"] for item in items],
            sorted(item["channelId"] for item in items),
        )
        expected = {
            str(self.active_channel.public_id): {
                "channelId": str(self.active_channel.public_id),
                "label": "通知チャネル",
                "active": True,
                "deliveryAvailable": True,
                "unavailableReason": None,
            },
            str(self.inactive_channel.public_id): {
                "channelId": str(self.inactive_channel.public_id),
                "label": "停止チャネル",
                "active": False,
                "deliveryAvailable": False,
                "unavailableReason": "channel_inactive",
            },
            str(self.no_recipient_channel.public_id): {
                "channelId": str(self.no_recipient_channel.public_id),
                "label": "未登録チャネル",
                "active": True,
                "deliveryAvailable": True,
                "unavailableReason": None,
            },
        }
        self.assertEqual({item["channelId"]: item for item in items}, expected)
        body = str(response.json())
        for forbidden in (
            "Usubject-secret-canary",
            "channel-id-secret-canary",
            "Ubot-secret-canary",
            "fixed-token-canary",
            "fixed-recipient-canary",
        ):
            self.assertNotIn(forbidden, body)

    # テストケース: active ownerが選択channelのrecipient一覧を取得する
    # 期待値: friend／disabled／not_friend／unknownを理由付きstrict DTOで返す
    def test_active_owner_lists_safe_recipient_choices_in_stable_order(self):
        client = self.owner_client()
        scenarios = (
            (True, "friend", True, None),
            (False, "friend", False, "recipient_disabled"),
            (True, "not_friend", False, "not_friend"),
            (True, "unknown", False, "friendship_unknown"),
        )
        for enabled, friendship, available, reason in scenarios:
            self.friend.enabled = enabled
            self.friend.friendship_state = friendship
            self.friend.save(
                update_fields=("enabled", "friendship_state", "updated_at")
            )
            response = client.get(
                f"/api/deliveries/targets/channels/{self.active_channel.public_id}/"
                "recipients/"
            )

            self.assertEqual(response.status_code, 200)
            items = response.json()["items"]
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(
                set(item),
                {
                    "recipientId",
                    "displayName",
                    "enabled",
                    "friendshipState",
                    "deliveryAvailable",
                    "unavailableReason",
                },
            )
            self.assertEqual(item["recipientId"], str(self.friend.public_id))
            self.assertEqual(item["displayName"], "Owner display")
            self.assertEqual(
                (
                    item["enabled"],
                    item["friendshipState"],
                    item["deliveryAvailable"],
                    item["unavailableReason"],
                ),
                (enabled, friendship, available, reason),
            )
            body = str(response.json())
            self.assertNotIn("Usubject-secret-canary", body)
            self.assertNotIn("fixed-recipient-canary", body)

    # テストケース: 正当なchannelにrecipientが一件も登録されていない
    # 期待値: targetの存在を隠さず空の選択肢として200を返し送信候補を作らない
    def test_channel_without_recipients_returns_empty_items(self):
        response = self.owner_client().get(
            f"/api/deliveries/targets/channels/{self.no_recipient_channel.public_id}/"
            "recipients/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": []})

    # テストケース: 未認証者とunlink pending ownerがtarget一覧を要求する
    # 期待値: adapterを呼ぶ前にそれぞれ401／403で拒否する
    def test_inactive_sessions_are_rejected_before_target_adapter(self):
        anonymous = APIClient().get("/api/deliveries/targets/channels/")
        pending_client = self.owner_client()
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.repository.begin_unlink(owner, uuid4())

        with patch(
            "lineaccounts.delivery_repositories.DeliveryTargetDirectory.list_channels",
            side_effect=AssertionError("adapter must not run"),
        ):
            pending = pending_client.get("/api/deliveries/targets/channels/")

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(
            anonymous.json()["error"]["code"], "authentication_required"
        )
        self.assertEqual(pending.status_code, 403)
        self.assertIn(
            pending.json()["error"]["code"],
            ("owner_not_allowed", "owner_operation_blocked"),
        )

    # テストケース: recipient routeへ非canonical UUIDまたはowner範囲外channelを指定する
    # 期待値: 非canonical UUIDは400、hidden targetは同じ404分類で存在関係を開示しない
    def test_recipient_route_validates_canonical_uuid_and_hides_targets(self):
        client = self.owner_client()
        invalid = client.get(
            f"/api/deliveries/targets/channels/{str(self.active_channel.public_id).replace('-', '')}/"
            "recipients/"
        )
        other_provider = self._channel(
            "別provider",
            provider_id="0099999999",
        )
        missing = client.get(
            f"/api/deliveries/targets/channels/{uuid4()}/recipients/"
        )
        mismatched = client.get(
            f"/api/deliveries/targets/channels/{other_provider.public_id}/"
            "recipients/"
        )

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "validation_error")
        for response in (missing, mismatched):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(
                response.json()["error"]["code"], "target_not_available"
            )
            self.assertEqual(set(response.json()), {"error"})

    # テストケース: target adapterのDB読み取りが失敗する
    # 期待値: raw例外を露出せずchannel／recipientともsafe 503へ縮約する
    def test_target_storage_failure_maps_to_safe_503(self):
        client = self.owner_client()
        with patch(
            "lineaccounts.delivery_repositories.DeliveryTargetDirectory.list_channels",
            side_effect=DatabaseError("database-secret-canary"),
        ):
            channels = client.get("/api/deliveries/targets/channels/")
        with patch(
            "lineaccounts.delivery_repositories.DeliveryTargetDirectory.list_recipients",
            side_effect=DatabaseError("database-secret-canary"),
        ):
            recipients = client.get(
                f"/api/deliveries/targets/channels/{self.active_channel.public_id}/"
                "recipients/"
            )

        for response in (channels, recipients):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.json()["error"]["code"], "storage_unavailable"
            )
            self.assertNotIn("database-secret-canary", str(response.json()))
