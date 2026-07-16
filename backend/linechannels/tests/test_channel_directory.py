import uuid

from django.test import TestCase

from linechannels.models import LineChannel, LineChannelCredential
from linechannels.repositories import DjangoLineChannelDirectory
from linechannels.types import LinkableChannelSummary


class DjangoLineChannelDirectoryTests(TestCase):
    def make_channel(self, *, provider_id, is_active=True, label="directory channel"):
        suffix = uuid.uuid4().hex
        return LineChannel.objects.create(
            messaging_api_channel_id=str(int(suffix[:12], 16)),
            bot_user_id=f"U{suffix}",
            label=label,
            provider_id=provider_id,
            is_active=is_active,
        )

    def test_lists_only_active_provider_bound_channels_as_safe_projection(self):
        included = self.make_channel(provider_id="000123", label="included")
        self.make_channel(provider_id=None, label="legacy")
        self.make_channel(provider_id="456", is_active=False, label="inactive")
        LineChannelCredential.objects.create(
            line_channel=included,
            access_token_ciphertext=b"access-cipher-canary",
            channel_secret_ciphertext=b"secret-cipher-canary",
        )

        result = DjangoLineChannelDirectory().list_active_bound()

        self.assertEqual(
            result,
            (LinkableChannelSummary(included.public_id, "included", "000123", True),),
        )
        rendered = repr(result)
        for forbidden in ("messaging_api_channel_id", "bot_user_id", "credential", "cipher-canary"):
            self.assertNotIn(forbidden, rendered)

    def test_get_returns_bound_channel_regardless_of_active_state_and_hides_unbound(self):
        inactive = self.make_channel(provider_id="123", is_active=False)
        legacy = self.make_channel(provider_id=None)
        directory = DjangoLineChannelDirectory()

        self.assertEqual(
            directory.get(inactive.public_id),
            LinkableChannelSummary(inactive.public_id, inactive.label, "123", False),
        )
        self.assertIsNone(directory.get(legacy.public_id))
        self.assertIsNone(directory.get(uuid.uuid4()))
