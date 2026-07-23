from uuid import uuid4

from django.test import TestCase

from lineaccounts.interaction_repositories import DjangoInteractionAccountDirectory
from lineaccounts.models import DeliveryRecipient, LineIdentity, OwnerAccount
from lineaccounts.types import LineSubject
from linechannels.models import LineChannel
from lineinteractions.types import (
    LinkedInteractionUserMissing,
    VerifiedInteractionUser,
)


class InteractionAccountDirectoryTests(TestCase):
    def setUp(self):
        self.provider_id = "0012345678"
        self.subject = "U" + "a" * 32
        self.identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=self.subject,
            display_name="Owner",
        )
        self.owner, _ = OwnerAccount.objects.get_or_create(slot=1)
        self.owner.state = OwnerAccount.State.ACTIVE
        self.owner.identity = self.identity
        self.owner.save(update_fields=("state", "identity"))
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id="1234567890",
            bot_user_id="U" + "b" * 32,
            label="channel",
            provider_id=self.provider_id,
            is_active=True,
        )
        self.recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.channel,
            enabled=False,
            friendship_state=DeliveryRecipient.FriendshipState.NOT_FRIEND,
        )
        self.directory = DjangoInteractionAccountDirectory()

    # テストケース: active owner/provider/subject/channel recipientを完全一致照合する
    # 期待値: enabled/friendship状態に依存せず安全なpublic IDだけを返す
    def test_resolves_only_complete_existing_link(self):
        result = self.directory.resolve_linked(
            channel_public_id=self.channel.public_id,
            provider_id=self.provider_id,
            subject=LineSubject(self.subject),
        )

        self.assertIsInstance(result, VerifiedInteractionUser)
        self.assertEqual(result.identity_public_id, self.identity.public_id)
        self.assertEqual(result.recipient_public_id, self.recipient.public_id)

    # テストケース: owner/provider/subject/channelの各mismatchを照合する
    # 期待値: missingを返しidentity/owner/recipientを作成・更新しない
    def test_mismatches_are_read_only_missing(self):
        baseline = (
            LineIdentity.objects.count(),
            OwnerAccount.objects.count(),
            DeliveryRecipient.objects.count(),
        )
        other_channel = LineChannel.objects.create(
            messaging_api_channel_id="9876543210",
            bot_user_id="U" + "c" * 32,
            label="other",
            provider_id=self.provider_id,
            is_active=True,
        )
        cases = (
            {
                "channel_public_id": other_channel.public_id,
                "provider_id": self.provider_id,
                "subject": LineSubject(self.subject),
            },
            {
                "channel_public_id": self.channel.public_id,
                "provider_id": "9999999999",
                "subject": LineSubject(self.subject),
            },
            {
                "channel_public_id": self.channel.public_id,
                "provider_id": self.provider_id,
                "subject": LineSubject("U" + "d" * 32),
            },
        )

        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assertIsInstance(
                    self.directory.resolve_linked(**arguments),
                    LinkedInteractionUserMissing,
                )
        self.owner.state = OwnerAccount.State.DEAUTHORIZATION_PENDING
        self.owner.unlink_generation = uuid4()
        self.owner.save(update_fields=("state", "unlink_generation"))
        self.assertIsInstance(
            self.directory.resolve_linked(
                channel_public_id=self.channel.public_id,
                provider_id=self.provider_id,
                subject=LineSubject(self.subject),
            ),
            LinkedInteractionUserMissing,
        )
        self.assertEqual(
            (
                LineIdentity.objects.count(),
                OwnerAccount.objects.count(),
                DeliveryRecipient.objects.count(),
            ),
            baseline,
        )
