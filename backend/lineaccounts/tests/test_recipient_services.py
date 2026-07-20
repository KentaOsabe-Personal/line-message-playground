from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from lineaccounts.gateway import (
    FriendshipSucceeded,
    InvalidLineProof,
    VerifiedLineIdentity,
    VerifyUserTokenSucceeded,
)
from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerSession
from lineaccounts.recipient_services import (
    DefaultRecipientService,
    RecipientMutationFailed,
    RecipientMutationSucceeded,
)
from lineaccounts.repositories import DjangoAccountRepository, NewRecipient
from lineaccounts.runtime import LiffLinkedChannelPolicy
from lineaccounts.types import LineSubject, UserAccessToken
from linechannels.models import LineChannel
from linechannels.repositories import DjangoLineChannelDirectory


class RecipientChannelListingTests(TestCase):
    def setUp(self):
        self.provider_id = "0012345678"
        self.repository = DjangoAccountRepository()
        identity = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(f"U{uuid4().hex}"),
            display_name="Owner",
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.identity = self.repository.upsert_identity(identity)
            self.owner = self.repository.bind_owner_identity(
                owner, self.identity.public_id
            )
        self.service = DefaultRecipientService(
            DjangoLineChannelDirectory(), self.repository
        )

    def channel(self, label, *, provider_id=None, active=True):
        return LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label=label,
            provider_id=provider_id or self.provider_id,
            is_active=active,
        )

    def link(self, channel, *, enabled=True, friendship="unknown"):
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            recipient = self.repository.create_recipient(
                owner,
                NewRecipient(
                    identity_id=self.identity.public_id,
                    channel_id=channel.public_id,
                    friendship_state=friendship,
                ),
            )
            if not enabled:
                recipient = self.repository.set_recipient_enabled(
                    owner,
                    self.identity.public_id,
                    recipient.public_id,
                    False,
                )
        return recipient

    # テストケース: provider一致active channelと既存recipient channelを一覧する
    # 期待値: 他providerを除外しinactive既存linkを含むchannel UUID順の安全な和集合を返す
    def test_lists_provider_active_channels_union_existing_links(self):
        active = self.channel("未登録active")
        inactive = self.channel("登録済みinactive", active=False)
        other = self.channel("他provider", provider_id="0099999999")
        self.link(inactive)

        items = self.service.list_channels(self.identity.public_id)

        self.assertEqual(
            {item.channel_id for item in items},
            {active.public_id, inactive.public_id},
        )
        self.assertNotIn(other.public_id, {item.channel_id for item in items})
        self.assertEqual(
            [item.channel_id for item in items],
            sorted((active.public_id, inactive.public_id), key=str),
        )

    # テストケース: active friend recipientとdisabled/unknown/inactiveのlinkを一覧する
    # 期待値: deliveryAvailableをrecipient enabled・friend・channel activeの積で導出する
    def test_projects_link_and_delivery_states_without_secret_metadata(self):
        deliverable = self.channel("配信可能")
        disabled = self.channel("停止中")
        inactive = self.channel("チャネル停止", active=False)
        self.link(deliverable, friendship=DeliveryRecipient.FriendshipState.FRIEND)
        self.link(
            disabled,
            enabled=False,
            friendship=DeliveryRecipient.FriendshipState.FRIEND,
        )
        self.link(
            inactive,
            friendship=DeliveryRecipient.FriendshipState.FRIEND,
        )

        items = {
            item.channel_label: item
            for item in self.service.list_channels(self.identity.public_id)
        }

        self.assertTrue(items["配信可能"].delivery_available)
        self.assertEqual(items["配信可能"].link_state, "linked_enabled")
        self.assertFalse(items["停止中"].delivery_available)
        self.assertEqual(items["停止中"].link_state, "linked_disabled")
        self.assertFalse(items["チャネル停止"].delivery_available)
        self.assertEqual(items["チャネル停止"].channel_state, "inactive")

    # テストケース: 未登録のactive channelを一覧する
    # 期待値: unknown friendship・unlinked・配信不可として秘密情報なしで表示する
    def test_projects_unlinked_active_channel_as_unknown_and_unavailable(self):
        channel = self.channel("候補")

        item = self.service.list_channels(self.identity.public_id)[0]

        self.assertEqual(item.channel_id, channel.public_id)
        self.assertEqual(item.link_state, "unlinked")
        self.assertEqual(item.friendship_state, "unknown")
        self.assertFalse(item.delivery_available)
        self.assertIsNone(item.recipient_id)
        self.assertNotIn("messaging", repr(item).lower())
        self.assertNotIn("bot_user", repr(item).lower())

    # テストケース: recipient登録後にchannelのprovider変更と無効化が行われる
    # 期待値: 既存linkは一覧から消さずinactive・配信不可の安全な状態で保持する
    def test_keeps_existing_link_after_channel_provider_changes(self):
        channel = self.channel("provider変更対象")
        recipient = self.link(
            channel,
            friendship=DeliveryRecipient.FriendshipState.FRIEND,
        )
        channel.provider_id = "0099999999"
        channel.is_active = False
        channel.save(update_fields=("provider_id", "is_active", "updated_at"))

        items = self.service.list_channels(self.identity.public_id)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].recipient_id, recipient.public_id)
        self.assertEqual(items[0].channel_state, "inactive")
        self.assertFalse(items[0].delivery_available)

    # テストケース: inactiveな既存recipientを1件と12件に増やしてチャネル一覧を取得する。
    # 期待値: query数は固定で、credential tableをjoinせず安全なprojectionだけを返す。
    def test_channel_listing_has_a_fixed_query_budget_without_credential_join(self):
        inactive_channels = [self.channel(f"停止-{index}", active=False) for index in range(12)]
        for channel in inactive_channels:
            self.link(channel)

        with CaptureQueriesContext(connection) as queries:
            items = self.service.list_channels(self.identity.public_id)

        self.assertEqual(len(items), 12)
        self.assertEqual(len(queries), 3)
        sql = "\n".join(query["sql"].lower() for query in queries)
        self.assertNotIn("linechannels_linechannelcredential", sql)


class _RecipientGatewayStub:
    def __init__(self, identity, *, friendship=True, verification=None):
        self.identity = identity
        self.friendship = friendship
        self.verification = verification
        self.verify_calls = 0
        self.friendship_calls = 0

    def verify_user_access_token(self, token, expected_subject):
        self.verify_calls += 1
        return self.verification or VerifyUserTokenSucceeded(expected_subject)

    def get_friendship(self, token):
        self.friendship_calls += 1
        return FriendshipSucceeded(self.friendship)


class RecipientRegistrationTests(TestCase):
    def setUp(self):
        self.provider_id = "0012345678"
        self.repository = DjangoAccountRepository()
        self.subject = LineSubject(f"U{uuid4().hex}")
        identity = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=self.subject,
            display_name="Owner",
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.identity = self.repository.upsert_identity(identity)
            self.repository.bind_owner_identity(owner, self.identity.public_id)
        self.direct = self.channel("LIFF direct")
        self.gateway = _RecipientGatewayStub(self.identity)

    def channel(self, label, *, provider_id=None, active=True):
        return LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label=label,
            provider_id=provider_id or self.provider_id,
            is_active=active,
        )

    def service(self, gateway=None):
        return DefaultRecipientService(
            DjangoLineChannelDirectory(),
            self.repository,
            gateway or self.gateway,
            LiffLinkedChannelPolicy(self.direct.public_id),
        )

    # テストケース: LIFF direct channelへfresh tokenでrecipientを登録する
    # 期待値: 本人binding後のfriendshipを保存し配信可能なsafe projectionを返す
    def test_registers_direct_recipient_with_verified_friendship(self):
        result = self.service().register(
            self.identity.public_id,
            self.direct.public_id,
            UserAccessToken("fresh-token"),
        )

        self.assertIsInstance(result, RecipientMutationSucceeded)
        self.assertEqual(result.recipient.friendship_state, "friend")
        self.assertTrue(result.recipient.delivery_available)
        self.assertEqual(self.gateway.verify_calls, 1)
        self.assertEqual(self.gateway.friendship_calls, 1)

    # テストケース: non-direct channelへrecipientを登録する
    # 期待値: LINEを呼ばずfriendship unknownとして保存し配信不可にする
    def test_registers_non_direct_as_unknown_without_line_call(self):
        channel = self.channel("non-direct")

        result = self.service().register(
            self.identity.public_id, channel.public_id, None
        )

        self.assertIsInstance(result, RecipientMutationSucceeded)
        self.assertEqual(result.recipient.friendship_state, "unknown")
        self.assertFalse(result.recipient.delivery_available)
        self.assertEqual(self.gateway.verify_calls, 0)
        self.assertEqual(self.gateway.friendship_calls, 0)

    # テストケース: 同じidentity/channelへrecipient登録を再送する
    # 期待値: duplicateを同じ既存projectionへ収束させ永続行を増やさない
    def test_duplicate_registration_converges_to_existing_recipient(self):
        service = self.service()
        first = service.register(
            self.identity.public_id,
            self.direct.public_id,
            UserAccessToken("fresh-token"),
        )
        second = service.register(
            self.identity.public_id,
            self.direct.public_id,
            None,
        )

        self.assertIsInstance(first, RecipientMutationSucceeded)
        self.assertEqual(first, second)
        self.assertEqual(DeliveryRecipient.objects.count(), 1)
        self.assertEqual(self.gateway.verify_calls, 1)
        self.assertEqual(self.gateway.friendship_calls, 1)

    # テストケース: gatewayまたはLIFF linked policy未注入でrecipient登録する
    # 期待値: non-directへfail openせずmutation前に安全な一時利用不可へ収束する
    def test_registration_fails_closed_without_required_dependencies(self):
        channel = self.channel("登録対象")
        services = (
            DefaultRecipientService(
                DjangoLineChannelDirectory(),
                self.repository,
                None,
                LiffLinkedChannelPolicy(self.direct.public_id),
            ),
            DefaultRecipientService(
                DjangoLineChannelDirectory(),
                self.repository,
                self.gateway,
                None,
            ),
        )

        for service in services:
            result = service.register(
                self.identity.public_id, channel.public_id, None
            )
            self.assertEqual(result, RecipientMutationFailed("line_unavailable"))
            self.assertEqual(DeliveryRecipient.objects.count(), 0)

    # テストケース: missing・inactive・provider不一致channelへ登録する
    # 期待値: mutation前に安全な分類で拒否しrecipientを作成しない
    def test_rejects_unavailable_or_provider_mismatched_channel(self):
        inactive = self.channel("inactive", active=False)
        mismatch = self.channel("mismatch", provider_id="0099999999")
        cases = (
            (uuid4(), "channel_not_found"),
            (inactive.public_id, "channel_unavailable"),
            (mismatch.public_id, "provider_mismatch"),
        )

        for channel_id, code in cases:
            with self.subTest(code=code):
                result = self.service().register(
                    self.identity.public_id, channel_id, None
                )
                self.assertEqual(result, RecipientMutationFailed(code))
                self.assertEqual(DeliveryRecipient.objects.count(), 0)

    # テストケース: direct channelへtokenなしまたは無効な本人bindingで登録する
    # 期待値: friendship取得と永続化へ進まずinvalid_line_proofへ収束する
    def test_direct_registration_requires_valid_identity_binding(self):
        invalid_gateway = _RecipientGatewayStub(
            self.identity, verification=InvalidLineProof()
        )
        service = self.service(invalid_gateway)

        missing = service.register(
            self.identity.public_id, self.direct.public_id, None
        )
        invalid = service.register(
            self.identity.public_id,
            self.direct.public_id,
            UserAccessToken("invalid-token"),
        )

        self.assertEqual(missing, RecipientMutationFailed("invalid_line_proof"))
        self.assertEqual(invalid, RecipientMutationFailed("invalid_line_proof"))
        self.assertEqual(invalid_gateway.friendship_calls, 0)
        self.assertEqual(DeliveryRecipient.objects.count(), 0)


class RecipientStateMutationTests(TestCase):
    def setUp(self):
        self.provider_id = "0012345678"
        self.repository = DjangoAccountRepository()
        identity = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(f"U{uuid4().hex}"),
            display_name="Owner",
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.identity = self.repository.upsert_identity(identity)
            owner = self.repository.bind_owner_identity(
                owner, self.identity.public_id
            )
            self.session = self.repository.create_owner_session(
                owner, timezone.now() + timedelta(hours=8)
            )
        self.directory = DjangoLineChannelDirectory()
        self.service = DefaultRecipientService(
            self.directory, self.repository
        )

    def channel(self, label, *, provider_id=None, active=True):
        return LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label=label,
            provider_id=provider_id or self.provider_id,
            is_active=active,
        )

    def recipient(self, channel, *, friendship="friend", enabled=True):
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            recipient = self.repository.create_recipient(
                owner,
                NewRecipient(
                    identity_id=self.identity.public_id,
                    channel_id=channel.public_id,
                    friendship_state=friendship,
                ),
            )
            if not enabled:
                recipient = self.repository.set_recipient_enabled(
                    owner,
                    self.identity.public_id,
                    recipient.public_id,
                    False,
                )
        return recipient

    # テストケース: enabled recipientを無効化して再有効化する
    # 期待値: 同じ関係を保持し状態だけを変更しfriend/active時だけ配信可能へ戻る
    def test_disables_and_reenables_same_recipient_relationship(self):
        channel = self.channel("対象")
        recipient = self.recipient(channel)

        disabled = self.service.set_enabled(
            self.identity.public_id, recipient.public_id, False
        )
        enabled = self.service.set_enabled(
            self.identity.public_id, recipient.public_id, True
        )

        self.assertIsInstance(disabled, RecipientMutationSucceeded)
        self.assertEqual(disabled.recipient.link_state, "linked_disabled")
        self.assertFalse(disabled.recipient.delivery_available)
        self.assertEqual(enabled.recipient.recipient_id, recipient.public_id)
        self.assertEqual(enabled.recipient.link_state, "linked_enabled")
        self.assertTrue(enabled.recipient.delivery_available)
        self.assertEqual(DeliveryRecipient.objects.count(), 1)
        self.assertTrue(OwnerSession.objects.filter(public_id=self.session.public_id).exists())

    # テストケース: friendship unknown recipientを再有効化する
    # 期待値: enabledへ戻してもfriendではないため配信不可のままになる
    def test_unknown_friendship_stays_unavailable_when_enabled(self):
        channel = self.channel("unknown")
        recipient = self.recipient(channel, friendship="unknown", enabled=False)

        result = self.service.set_enabled(
            self.identity.public_id, recipient.public_id, True
        )

        self.assertIsInstance(result, RecipientMutationSucceeded)
        self.assertEqual(result.recipient.link_state, "linked_enabled")
        self.assertFalse(result.recipient.delivery_available)

    # テストケース: inactiveまたはprovider不一致channelのrecipientを再有効化する
    # 期待値: 安全な利用不可分類で拒否しdisabled状態を維持する
    def test_reenable_revalidates_channel_active_and_provider(self):
        inactive_channel = self.channel("inactive", active=False)
        mismatch_channel = self.channel(
            "mismatch", provider_id="0099999999"
        )
        inactive = self.recipient(inactive_channel, enabled=False)
        mismatch = self.recipient(mismatch_channel, enabled=False)

        inactive_result = self.service.set_enabled(
            self.identity.public_id, inactive.public_id, True
        )
        mismatch_result = self.service.set_enabled(
            self.identity.public_id, mismatch.public_id, True
        )

        self.assertEqual(
            inactive_result, RecipientMutationFailed("channel_unavailable")
        )
        self.assertEqual(
            mismatch_result, RecipientMutationFailed("provider_mismatch")
        )
        self.assertFalse(DeliveryRecipient.objects.get(public_id=inactive.public_id).enabled)
        self.assertFalse(DeliveryRecipient.objects.get(public_id=mismatch.public_id).enabled)

    # テストケース: 存在しないrecipientの状態変更を要求する
    # 期待値: recipient_not_foundを返し既存recipientへ変更を加えない
    def test_missing_recipient_does_not_mutate_other_links(self):
        channel = self.channel("existing")
        existing = self.recipient(channel)

        result = self.service.set_enabled(
            self.identity.public_id, uuid4(), False
        )

        self.assertEqual(result, RecipientMutationFailed("recipient_not_found"))
        self.assertTrue(
            DeliveryRecipient.objects.get(public_id=existing.public_id).enabled
        )


class RecipientUnlinkTests(TestCase):
    def setUp(self):
        self.provider_id = "0012345678"
        self.repository = DjangoAccountRepository()
        identity = VerifiedLineIdentity(
            provider_id=self.provider_id,
            subject=LineSubject(f"U{uuid4().hex}"),
            display_name="Owner",
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            self.identity = self.repository.upsert_identity(identity)
            owner = self.repository.bind_owner_identity(
                owner, self.identity.public_id
            )
            self.sessions = (
                self.repository.create_owner_session(
                    owner, timezone.now() + timedelta(hours=8)
                ),
                self.repository.create_owner_session(
                    owner, timezone.now() + timedelta(hours=8)
                ),
            )
        self.service = DefaultRecipientService(
            DjangoLineChannelDirectory(), self.repository
        )

    def recipient(self, label):
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label=label,
            provider_id=self.provider_id,
            is_active=True,
        )
        with transaction.atomic():
            owner = self.repository.lock_owner_account()
            return self.repository.create_recipient(
                owner,
                NewRecipient(
                    identity_id=self.identity.public_id,
                    channel_id=channel.public_id,
                    friendship_state="unknown",
                ),
            )

    # テストケース: 2つのrecipientから選択した1つをチャネル別解除する
    # 期待値: 対象だけを削除しidentity・他recipient・全端末sessionを維持する
    def test_unlinks_only_selected_recipient(self):
        selected = self.recipient("解除対象")
        retained = self.recipient("維持対象")

        result = self.service.unlink(
            self.identity.public_id, selected.public_id
        )

        self.assertIsInstance(result, RecipientMutationSucceeded)
        self.assertEqual(result.recipient.channel_id, selected.channel_id)
        self.assertEqual(result.recipient.link_state, "unlinked")
        self.assertIsNone(result.recipient.recipient_id)
        self.assertFalse(
            DeliveryRecipient.objects.filter(public_id=selected.public_id).exists()
        )
        self.assertTrue(
            DeliveryRecipient.objects.filter(public_id=retained.public_id).exists()
        )
        self.assertTrue(
            LineIdentity.objects.filter(public_id=self.identity.public_id).exists()
        )
        self.assertEqual(OwnerSession.objects.count(), 2)

    # テストケース: 存在しないrecipientのチャネル別解除を要求する
    # 期待値: recipient_not_foundを返し他recipientと全sessionを変更しない
    def test_missing_unlink_target_does_not_mutate_other_state(self):
        retained = self.recipient("維持対象")

        result = self.service.unlink(self.identity.public_id, uuid4())

        self.assertEqual(result, RecipientMutationFailed("recipient_not_found"))
        self.assertTrue(
            DeliveryRecipient.objects.filter(public_id=retained.public_id).exists()
        )
        self.assertEqual(OwnerSession.objects.count(), 2)
