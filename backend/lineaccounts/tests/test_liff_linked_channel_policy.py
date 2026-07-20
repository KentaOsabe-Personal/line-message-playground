import uuid
from unittest.mock import Mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from lineaccounts.runtime import (
    LineAccountRuntime,
    OwnerEligibilityUnavailable,
    SecretValue,
    resolve_liff_linked_channel_policy,
)
from linechannels.types import LinkableChannelSummary


class LiffLinkedChannelPolicyTests(SimpleTestCase):
    def setUp(self):
        self.public_id = uuid.uuid4()
        self.runtime = LineAccountRuntime(
            channel_id="123",
            channel_secret=SecretValue("secret"),
            provider_id="000456",
            linked_channel_public_id=self.public_id,
            owner_eligibility=OwnerEligibilityUnavailable(),
        )

    # テストケース: runtimeと同じproviderへboundされたLIFF直結チャネルを解決する。
    # 期待値: 対象UUIDだけをdirectとするimmutable policyが得られる。
    def test_resolves_matching_bound_channel_into_immutable_policy(self):
        directory = Mock()
        directory.get.return_value = LinkableChannelSummary(
            self.public_id, "direct", "000456", True
        )

        policy = resolve_liff_linked_channel_policy(self.runtime, directory)

        self.assertTrue(policy.is_direct(self.public_id))
        self.assertFalse(policy.is_direct(uuid.uuid4()))
        directory.get.assert_called_once_with(self.public_id)

    # テストケース: LIFF直結チャネルが未設定・unboundまたはprovider不一致である。
    # 期待値: account操作を有効化せず同じ安全な設定エラーでfail closedになる。
    def test_fails_closed_for_missing_unbound_or_provider_mismatch(self):
        cases = (
            None,
            LinkableChannelSummary(self.public_id, "direct", "999", True),
        )
        for value in cases:
            with self.subTest(value=value):
                directory = Mock()
                directory.get.return_value = value
                with self.assertRaisesMessage(
                    ImproperlyConfigured, "LINE_ACCOUNT_CHANNEL_POLICY_INVALID"
                ):
                    resolve_liff_linked_channel_policy(self.runtime, directory)
