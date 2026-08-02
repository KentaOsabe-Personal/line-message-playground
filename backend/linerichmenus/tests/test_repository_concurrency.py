from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from django.db import close_old_connections
from django.test import TransactionTestCase

from linerichmenus.models import ManagedRichMenu, RichMenuOperation
from linerichmenus.repository import (
    AcceptedOperation,
    DjangoRichMenuRepository,
    OperationAccepted,
    OperationConflict,
)
from linerichmenus.types import OperationKind

from .test_repository_acceptance import LockedFence


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class RichMenuRepositoryConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.command = AcceptedOperation(
            operation_id=uuid4(),
            channel_public_id=uuid4(),
            owner_identity_public_id=uuid4(),
            provider_id="0012345678",
            expected_channel_revision=NOW,
            kind=OperationKind.APPLY,
            subject_operation_id=None,
            target_resource_id=None,
            request_fingerprint="a" * 64,
            confirmation_usage_digest="b" * 64,
            configuration_snapshot={
                "version": 1,
                "templateId": "jp-link-one",
                "templateVersion": 1,
                "fields": [
                    {"displayName": "例", "uri": "https://example.com/"}
                ],
            },
            candidate_image_digest="c" * 64,
        )

    # テストケース: 同一channelへ異なる二つのapply operationを別connectionから同時受付する。
    # 期待値: channel lockで一件だけを予約し、他方を競合へ収束させて候補を重複作成しない。
    def test_concurrent_accept_keeps_one_active_operation_and_candidate(self):
        first = self.command
        second = replace(
            self.command,
            operation_id=uuid4(),
            request_fingerprint="d" * 64,
            confirmation_usage_digest="e" * 64,
        )

        def accept(command):
            close_old_connections()
            try:
                return DjangoRichMenuRepository(
                    reference_fence=LockedFence()
                ).accept(command)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(accept, (first, second)))

        self.assertEqual(sum(isinstance(item, OperationAccepted) for item in results), 1)
        self.assertEqual(sum(isinstance(item, OperationConflict) for item in results), 1)
        self.assertEqual(RichMenuOperation.objects.count(), 1)
        self.assertEqual(ManagedRichMenu.objects.count(), 1)
