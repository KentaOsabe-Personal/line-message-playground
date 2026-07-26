import hashlib
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

from django.test import SimpleTestCase, TestCase

from lineaccounts.delivery_repositories import (
    DeliveryTargetDirectory,
    build_target_revision,
)
from lineaccounts.models import LineIdentity, OwnerAccount
from linechannels.models import LineChannel, LineChannelCredential


class TargetRevisionBuilderTests(SimpleTestCase):
    owner_identity_id = UUID("11111111-1111-4111-8111-111111111111")
    channel_id = UUID("22222222-2222-4222-8222-222222222222")
    recipient_id = UUID("33333333-3333-4333-8333-333333333333")
    channel_updated_at = datetime(2026, 7, 26, 1, 2, 3, 456789, tzinfo=UTC)
    recipient_updated_at = datetime(2026, 7, 26, 4, 5, 6, 7, tzinfo=UTC)

    def _build(self, **overrides: object):
        values = {
            "owner_identity_public_id": self.owner_identity_id,
            "channel_public_id": self.channel_id,
            "provider_id": "0012345678",
            "channel_active": True,
            "channel_updated_at": self.channel_updated_at,
            "recipient_public_id": self.recipient_id,
            "recipient_enabled": True,
            "friendship_state": "friend",
            "recipient_updated_at": self.recipient_updated_at,
        }
        values.update(overrides)
        return build_target_revision(**values)

    @staticmethod
    def _expected_digest(parts: tuple[str, ...]) -> str:
        canonical = b"".join(
            len(encoded).to_bytes(4, "big") + encoded
            for part in parts
            for encoded in (part.encode("utf-8"),)
        )
        return hashlib.sha256(canonical).hexdigest()

    # テストケース: 同じlive targetを同値の異なるtimezone表現からrevision化する
    # 期待値: v1長さprefix canonical encodingの同じSHA-256 digestへ収束する
    def test_builds_stable_v1_digest_with_utc_fixed_microseconds(self):
        expected = self._expected_digest(
            (
                "v1",
                str(self.owner_identity_id),
                str(self.channel_id),
                "0012345678",
                "1",
                "2026-07-26T01:02:03.456789Z",
                str(self.recipient_id),
                "1",
                "friend",
                "2026-07-26T04:05:06.000007Z",
            )
        )

        first = self._build()
        equivalent_timezone = self._build(
            channel_updated_at=self.channel_updated_at.astimezone(
                timezone(timedelta(hours=9))
            ),
            recipient_updated_at=self.recipient_updated_at.astimezone(
                timezone(timedelta(hours=-4))
            ),
        )

        self.assertEqual(first.digest, expected)
        self.assertEqual(equivalent_timezone, first)

    # テストケース: 配信可否に関わる各target構成要素を一つずつ変更する
    # 期待値: owner・channel・provider・状態・updated revisionの各変更でdigestが変わる
    def test_changes_digest_for_every_revision_component(self):
        baseline = self._build()
        changes = (
            {"owner_identity_public_id": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")},
            {"channel_public_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")},
            {"provider_id": "0099999999"},
            {"channel_active": False},
            {"channel_updated_at": self.channel_updated_at + timedelta(microseconds=1)},
            {"recipient_public_id": UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")},
            {"recipient_enabled": False},
            {"friendship_state": "not_friend"},
            {
                "recipient_updated_at": self.recipient_updated_at
                + timedelta(microseconds=1)
            },
        )

        for change in changes:
            with self.subTest(change=change):
                self.assertNotEqual(self._build(**change), baseline)

    # テストケース: channel・recipient状態を変更後に元へ戻しupdated_atだけを進める
    # 期待値: いずれのlive状態が元と同じでも古い確認とは異なるrevisionになる
    def test_updated_revision_distinguishes_state_change_then_revert(self):
        original = self._build()

        reverted_targets = (
            self._build(
                channel_active=True,
                channel_updated_at=self.channel_updated_at + timedelta(seconds=1),
            ),
            self._build(
                recipient_enabled=True,
                friendship_state="friend",
                recipient_updated_at=self.recipient_updated_at
                + timedelta(seconds=1),
            ),
        )

        for reverted in reverted_targets:
            with self.subTest(reverted=reverted):
                self.assertNotEqual(reverted, original)

    # テストケース: canonical revision builderへ不正な境界値を渡す
    # 期待値: naive datetime・非UUID・非bool・未知friendshipをdigest化せず拒否する
    def test_rejects_noncanonical_boundary_values(self):
        invalid_changes = (
            {"owner_identity_public_id": str(self.owner_identity_id)},
            {"channel_active": 1},
            {"channel_updated_at": self.channel_updated_at.replace(tzinfo=None)},
            {"recipient_enabled": 0},
            {"friendship_state": "blocked"},
        )

        for change in invalid_changes:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    self._build(**change)


class DeliveryTargetDirectoryChannelTests(TestCase):
    provider_id = "0012345678"

    def setUp(self):
        self.identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=f"U{uuid4().hex}",
            display_name="Owner",
        )
        OwnerAccount.objects.get_or_create(slot=1)
        OwnerAccount.objects.filter(slot=1).update(
            state=OwnerAccount.State.ACTIVE,
            identity=self.identity,
        )
        self.directory = DeliveryTargetDirectory()

    def _channel(
        self,
        label,
        *,
        provider_id=None,
        active=True,
        with_credentials=False,
    ):
        channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=f"U{uuid4().hex}",
            label=label,
            provider_id=provider_id or self.provider_id,
            is_active=active,
        )
        if with_credentials:
            LineChannelCredential.objects.create(
                line_channel=channel,
                access_token_ciphertext=b"encrypted-token-canary",
                channel_secret_ciphertext=b"encrypted-secret-canary",
            )
        return channel

    # テストケース: active ownerと同providerに登録された有効・無効channelを一覧する
    # 期待値: 他providerを除外し、有効性と安全な理由をopaque UUID順で投影する
    def test_lists_same_provider_channels_with_safe_availability(self):
        active = self._channel("配信チャネル")
        inactive = self._channel("停止チャネル", active=False)
        self._channel("別provider", provider_id="0099999999")

        choices = self.directory.list_channels(self.identity.public_id)

        self.assertEqual(
            choices,
            tuple(
                sorted(
                    (
                        self._expected_choice(active, available=True, reason=None),
                        self._expected_choice(
                            inactive,
                            available=False,
                            reason="channel_inactive",
                        ),
                    ),
                    key=lambda choice: str(choice.channel_public_id),
                )
            ),
        )

    # テストケース: credentialを持つchannelと秘密のowner identityから選択肢を作る
    # 期待値: summaryにはlabel・opaque ID・active・availability・理由以外を含めない
    def test_projection_excludes_credentials_subject_and_fixed_targets(self):
        channel = self._channel(
            "秘密非露出",
            with_credentials=True,
        )

        choice = self.directory.list_channels(self.identity.public_id)[0]

        self.assertEqual(
            set(choice.__dataclass_fields__),
            {
                "channel_public_id",
                "label",
                "active",
                "available",
                "unavailable_reason",
            },
        )
        projection = repr(choice)
        for secret in (
            self.identity.subject,
            "encrypted-token-canary",
            "encrypted-secret-canary",
            channel.messaging_api_channel_id,
            channel.bot_user_id,
        ):
            self.assertNotIn(secret, projection)

    # テストケース: inactiveまたは別identityのowner identity IDから一覧する
    # 期待値: channelの存在やproviderを開示せず空の選択肢へ縮約する
    def test_requires_exact_active_owner_identity(self):
        self._channel("存在を隠す")
        other_identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=f"U{uuid4().hex}",
            display_name="Other",
        )

        for owner_identity_id in (
            other_identity.public_id,
            uuid4(),
        ):
            with self.subTest(owner_identity_id=owner_identity_id):
                self.assertEqual(
                    self.directory.list_channels(owner_identity_id),
                    (),
                )

        owner = OwnerAccount.objects.get(slot=1)
        owner.state = OwnerAccount.State.DEAUTHORIZATION_PENDING
        owner.unlink_generation = uuid4()
        owner.save(update_fields=("state", "unlink_generation", "updated_at"))
        self.assertEqual(
            self.directory.list_channels(self.identity.public_id),
            (),
        )

    @staticmethod
    def _expected_choice(channel, *, available, reason):
        from delivery.types import DeliveryChannelChoice

        return DeliveryChannelChoice(
            channel_public_id=channel.public_id,
            label=channel.label,
            active=channel.is_active,
            available=available,
            unavailable_reason=reason,
        )
