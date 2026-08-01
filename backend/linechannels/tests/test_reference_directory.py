from unittest.mock import Mock, patch
from uuid import uuid4

from django.db import DatabaseError, OperationalError
from django.test import SimpleTestCase

from delivery.models import DeliveryAttempt
from delivery.repositories import DjangoDeliveryReferenceProbe
from lineaccounts.models import DeliveryRecipient
from lineaccounts.repositories import DjangoRecipientReferenceProbe
from linechannels.reference_fence import ChannelReferenceDirectory
from linechannels.container import build_channel_reference_directory
from linefriendships.models import FriendshipSyncAudit
from linefriendships.repositories import DjangoFriendshipReferenceProbe
from lineinteractions.models import InteractionAudit
from lineinteractions.repositories import DjangoInteractionReferenceProbe
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.repositories import DjangoWebhookReferenceProbe


class RecordingProbe:
    def __init__(self, result=False, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def is_referenced(self, channel_public_id):
        self.calls.append(channel_public_id)
        if self.error is not None:
            raise self.error
        return self.result


class ChannelReferenceDirectoryTests(SimpleTestCase):
    # テストケース: production管理directoryを構築する
    # 期待値: recipient、delivery、webhook、friendship、interactionの固定順序になる
    def test_production_directory_uses_fixed_probe_order(self):
        directory = build_channel_reference_directory()

        self.assertEqual(
            tuple(type(probe).__name__ for probe in directory._probes),
            (
                "DjangoRecipientReferenceProbe",
                "DjangoDeliveryReferenceProbe",
                "DjangoWebhookReferenceProbe",
                "DjangoFriendshipReferenceProbe",
                "DjangoInteractionReferenceProbe",
            ),
        )

    # テストケース: 固定順序のprobe列で途中に参照が見つかる
    # 期待値: 最初の参照検出で停止し後続storeを照会しない
    def test_short_circuits_in_injected_probe_order(self):
        channel_id = uuid4()
        first = RecordingProbe()
        referenced = RecordingProbe(result=True)
        skipped = RecordingProbe(result=True)

        result = ChannelReferenceDirectory(
            (first, referenced, skipped)
        ).is_referenced(channel_id)

        self.assertEqual(result.status, "referenced")
        self.assertEqual(first.calls, [channel_id])
        self.assertEqual(referenced.calls, [channel_id])
        self.assertEqual(skipped.calls, [])

    # テストケース: probeがdeadlockまたは一般DB失敗を返す
    # 期待値: 削除を許可せず固定safe storage分類へ縮約する
    def test_storage_failure_fails_closed(self):
        for error, expected in (
            (OperationalError(1205, "secret-canary"), "storage_retryable"),
            (DatabaseError("secret-canary"), "storage_unavailable"),
        ):
            with self.subTest(expected=expected):
                result = ChannelReferenceDirectory(
                    (RecordingProbe(error=error),)
                ).is_referenced(uuid4())
            self.assertEqual(result.status, expected)
            self.assertNotIn("secret-canary", repr(result))

    # テストケース: 5種のprobeをそれぞれ呼び出す
    # 期待値: 各probeが自appの参照modelだけへexists queryを委譲する
    def test_each_probe_reads_only_its_owned_store(self):
        channel_id = uuid4()
        cases = (
            (DjangoRecipientReferenceProbe(), DeliveryRecipient),
            (DjangoDeliveryReferenceProbe(), DeliveryAttempt),
            (DjangoWebhookReferenceProbe(), WebhookEventReceipt),
            (DjangoFriendshipReferenceProbe(), FriendshipSyncAudit),
            (DjangoInteractionReferenceProbe(), InteractionAudit),
        )

        for probe, model in cases:
            scoped_manager = Mock()
            queryset = scoped_manager.filter.return_value
            queryset.exists.return_value = True
            with self.subTest(probe=type(probe).__name__), patch.object(
                model.objects, "using", return_value=scoped_manager
            ) as using:
                self.assertTrue(probe.is_referenced(channel_id))
            using.assert_called_once_with("default")
            scoped_manager.filter.assert_called_once()
            queryset.exists.assert_called_once_with()
