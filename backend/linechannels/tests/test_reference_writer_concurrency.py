import threading
from datetime import UTC, datetime
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.test import TransactionTestCase

from delivery.formatters import format_message_snapshot
from delivery.models import DeliveryAttempt
from delivery.repositories import DjangoAttemptRepository, build_request_fingerprint
from delivery.types import (
    AcceptedDeliveryCommand,
    AttemptAccepted,
    AttemptTargetUnavailable,
    LinkedTargetSnapshot,
    MessageSnapshot,
    OwnerIdentitySnapshot,
    OwnerPrincipal,
)
from lineaccounts.gateway import VerifiedLineIdentity
from lineaccounts.models import DeliveryRecipient, OwnerAccount
from lineaccounts.repositories import (
    AccountStateError,
    DjangoAccountRepository,
    NewRecipient,
)
from lineaccounts.types import LineSubject
from linechannels.container import build_channel_reference_directory
from linechannels.models import LineChannel
from linechannels.reference_fence import DjangoChannelReferenceFence
from linefriendships.models import FriendshipSyncAudit
from linefriendships.repositories import (
    DjangoFriendshipAuditRepository,
    FriendshipAuditStorageError,
)
from linefriendships.types import FriendshipAuditRecord
from lineinteractions.models import InteractionAudit
from lineinteractions.repositories import DjangoInteractionAuditRepository
from lineinteractions.types import InteractionAuditRecord
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.repositories import DjangoEventReceiptRepository
from linewebhooks.types import ReceiptCandidate, ReceiptChannelUnavailable


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class BlockingFence:
    def __init__(self) -> None:
        self._delegate = DjangoChannelReferenceFence()
        self.locked = threading.Event()
        self.release = threading.Event()

    def lock_existing(self, channel_public_id):
        result = self._delegate.lock_existing(channel_public_id)
        if result.status == "locked":
            self.locked.set()
            if not self.release.wait(5):
                raise RuntimeError("test fence release timed out")
        return result


class ReferenceWriterConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        OwnerAccount.objects.get_or_create(slot=1)
        repository = DjangoAccountRepository()
        identity = VerifiedLineIdentity(
            provider_id="001234",
            subject=LineSubject("U" + uuid4().hex),
            display_name="Owner",
        )
        with transaction.atomic():
            owner = repository.lock_owner_account()
            self.identity = repository.upsert_identity(identity)
            repository.bind_owner_identity(owner, self.identity.public_id)

    def _channel(self) -> LineChannel:
        return LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id="U" + uuid4().hex,
            label="参照競合",
            provider_id="001234",
            is_active=True,
        )

    def _writers(self, channel, fence):
        event_id = "01" + uuid4().hex[:24]
        recipient_repository = DjangoAccountRepository(reference_fence=fence)

        def recipient_writer():
            try:
                with transaction.atomic():
                    owner = recipient_repository.lock_owner_account()
                    return recipient_repository.create_recipient(
                        owner,
                        NewRecipient(
                            identity_id=self.identity.public_id,
                            channel_id=channel.public_id,
                            friendship_state="unknown",
                        ),
                    )
            except AccountStateError as error:
                return error.code

        target = LinkedTargetSnapshot(
            channel_public_id=channel.public_id,
            channel_label=channel.label,
            recipient_public_id=uuid4(),
            channel_active=True,
            recipient_enabled=True,
            friendship_state="friend",
        )
        message = format_message_snapshot("件名", "本文")
        owner = OwnerPrincipal(1)
        owner_identity = OwnerIdentitySnapshot(self.identity.public_id)
        delivery_command = AcceptedDeliveryCommand(
            operation_id=uuid4(),
            owner=owner,
            owner_identity=owner_identity,
            target=target,
            message=MessageSnapshot(
                subject=message.subject,
                body=message.body,
                formatted_text=message.formatted_text,
                fingerprint=message.fingerprint,
            ),
            request_fingerprint=build_request_fingerprint(
                owner=owner,
                owner_identity=owner_identity,
                channel_public_id=channel.public_id,
                recipient_public_id=target.recipient_public_id,
                message_fingerprint=message.fingerprint,
                receipt_requested=False,
            ),
            receipt_commitment=None,
        )

        return (
            (
                "recipient",
                recipient_writer,
                lambda: DeliveryRecipient.objects.filter(
                    line_channel__public_id=channel.public_id
                ).count(),
                lambda result: not isinstance(result, str),
            ),
            (
                "delivery",
                lambda: DjangoAttemptRepository(
                    reference_fence=fence,
                    clock=lambda: NOW,
                ).accept(delivery_command),
                lambda: DeliveryAttempt.objects.filter(
                    channel_public_id=channel.public_id
                ).count(),
                lambda result: isinstance(result, AttemptAccepted),
            ),
            (
                "webhook",
                lambda: DjangoEventReceiptRepository(fence).accept_batch(
                    (
                        ReceiptCandidate(
                            channel_public_id=channel.public_id,
                            webhook_event_id=event_id,
                            event_type="message",
                            occurred_at_ms=1,
                            is_redelivery=False,
                            initial_status="processing",
                        ),
                    )
                ),
                lambda: WebhookEventReceipt.objects.filter(
                    channel_public_id=channel.public_id
                ).count(),
                lambda result: isinstance(result, tuple),
            ),
            (
                "friendship",
                lambda: self._record_friendship(channel, event_id, fence),
                lambda: FriendshipSyncAudit.objects.filter(
                    channel_public_id=channel.public_id
                ).count(),
                lambda result: result == "recorded",
            ),
            (
                "interaction",
                lambda: DjangoInteractionAuditRepository(fence).record(
                    InteractionAuditRecord(
                        channel_public_id=channel.public_id,
                        webhook_event_id=event_id,
                        event_type="message",
                        operation_kind="command",
                        operation_identifier="connectivity_ping_v1",
                        interaction_outcome="command_processed",
                        reply_outcome="accepted",
                    )
                ),
                lambda: InteractionAudit.objects.filter(
                    channel_public_id=channel.public_id
                ).count(),
                lambda result: result == "recorded",
            ),
        )

    @staticmethod
    def _record_friendship(channel, event_id, fence):
        repository = DjangoFriendshipAuditRepository(fence)
        try:
            with transaction.atomic():
                repository.record(
                    FriendshipAuditRecord(
                        channel_public_id=channel.public_id,
                        webhook_event_id=event_id,
                        event_type="follow",
                        occurred_at_ms=1,
                        outcome="unlinked",
                        is_unblocked=True,
                    )
                )
        except FriendshipAuditStorageError as error:
            return error.code
        return "recorded"

    # テストケース: deleteがchannel lockを先に取得してから5 writerを開始する
    # 期待値: commit後のwriterは全てchannel不在へ収束し参照行を作成しない
    def test_delete_first_prevents_all_five_reference_writers(self):
        for name in ("recipient", "delivery", "webhook", "friendship", "interaction"):
            with self.subTest(writer=name):
                channel = self._channel()
                real_fence = DjangoChannelReferenceFence()
                writer = next(
                    item for item in self._writers(channel, real_fence) if item[0] == name
                )
                delete_locked = threading.Event()
                commit_delete = threading.Event()
                writer_started = threading.Event()
                outcomes = []

                def delete_first():
                    close_old_connections()
                    with transaction.atomic():
                        locked = real_fence.lock_existing(channel.public_id)
                        if locked.status != "locked":
                            raise AssertionError(locked)
                        LineChannel.objects.filter(public_id=channel.public_id).delete()
                        delete_locked.set()
                        if not commit_delete.wait(5):
                            raise RuntimeError("delete release timed out")
                    close_old_connections()

                def run_writer():
                    close_old_connections()
                    if not delete_locked.wait(5):
                        raise RuntimeError("delete lock timed out")
                    writer_started.set()
                    outcomes.append(writer[1]())
                    close_old_connections()

                delete_thread = threading.Thread(target=delete_first)
                writer_thread = threading.Thread(target=run_writer)
                delete_thread.start()
                writer_thread.start()
                self.assertTrue(writer_started.wait(5))
                commit_delete.set()
                delete_thread.join(5)
                writer_thread.join(5)

                self.assertFalse(delete_thread.is_alive())
                self.assertFalse(writer_thread.is_alive())
                self.assertEqual(writer[2](), 0)
                self.assertEqual(len(outcomes), 1)
                self.assertFalse(writer[3](outcomes[0]))
                self.assertTrue(
                    outcomes[0] == "channel_not_found"
                    or isinstance(outcomes[0], AttemptTargetUnavailable)
                    or isinstance(outcomes[0], ReceiptChannelUnavailable)
                )

    # テストケース: 5 writerがchannel lockを先に取得しdelete確認を競合させる
    # 期待値: writer commit後にdirectoryが参照を検出しchannelを削除しない
    def test_writer_first_makes_delete_observe_reference_for_all_five_stores(self):
        for name in ("recipient", "delivery", "webhook", "friendship", "interaction"):
            with self.subTest(writer=name):
                channel = self._channel()
                fence = BlockingFence()
                writer = next(
                    item for item in self._writers(channel, fence) if item[0] == name
                )
                outcomes = []
                delete_results = []

                def run_writer():
                    close_old_connections()
                    outcomes.append(writer[1]())
                    close_old_connections()

                def run_delete():
                    close_old_connections()
                    with transaction.atomic():
                        locked = DjangoChannelReferenceFence().lock_existing(
                            channel.public_id
                        )
                        if locked.status != "locked":
                            delete_results.append(locked.status)
                            return
                        check = build_channel_reference_directory().is_referenced(
                            channel.public_id
                        )
                        delete_results.append(check.status)
                        if check.status == "unreferenced":
                            LineChannel.objects.filter(
                                public_id=channel.public_id
                            ).delete()
                    close_old_connections()

                writer_thread = threading.Thread(target=run_writer)
                delete_thread = threading.Thread(target=run_delete)
                writer_thread.start()
                self.assertTrue(fence.locked.wait(5))
                delete_thread.start()
                fence.release.set()
                writer_thread.join(5)
                delete_thread.join(5)

                self.assertFalse(writer_thread.is_alive())
                self.assertFalse(delete_thread.is_alive())
                self.assertEqual(len(outcomes), 1)
                self.assertTrue(writer[3](outcomes[0]))
                self.assertEqual(delete_results, ["referenced"])
                self.assertTrue(
                    LineChannel.objects.filter(public_id=channel.public_id).exists()
                )
                self.assertEqual(writer[2](), 1)
