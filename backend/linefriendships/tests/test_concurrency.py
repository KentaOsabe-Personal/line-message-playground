import base64
import hashlib
import hmac
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from lineaccounts.friendship_repositories import (
    DjangoAccountProjectionRepository,
)
from lineaccounts.models import (
    DeliveryRecipient,
    LineIdentity,
    OwnerAccount,
    OwnerSession,
)
from lineaccounts.recipient_services import (
    DefaultRecipientService,
    RecipientMutationSucceeded,
)
from lineaccounts.repositories import DjangoAccountRepository
from linechannels import runtime
from linechannels.crypto import FernetCredentialCipher
from linechannels.models import LineChannel, LineChannelCredential
from linechannels.repositories import (
    DjangoLineChannelDirectory,
    DjangoLineChannelRepository,
)
from linechannels.services import DefaultLineChannelService
from linechannels.types import (
    AccessToken,
    ChannelSecret,
    CredentialContext,
    UpdateLineChannel,
)
from linefriendships.models import FriendshipSyncAudit
from linefriendships.services import DefaultFriendshipSyncService
from linewebhooks.container import build_webhook_ingress_service
from linewebhooks.models import WebhookEventReceipt
from linewebhooks.types import IngressAccepted


def _with_independent_connection(function):
    close_old_connections()
    try:
        return function()
    finally:
        close_old_connections()


class _BlockingLineChannelRepository(DjangoLineChannelRepository):
    def __init__(self, locked: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self._locked = locked
        self._release = release

    def get_for_update(self, public_id):
        value = super().get_for_update(public_id)
        self._locked.set()
        if not self._release.wait(timeout=5):
            raise RuntimeError("provider concurrency test timed out")
        return value


class _BlockingAccountProjectionRepository(DjangoAccountProjectionRepository):
    def __init__(self, locked: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self._locked = locked
        self._release = release

    def lock_target(self, **kwargs):
        value = super().lock_target(**kwargs)
        self._locked.set()
        if not self._release.wait(timeout=5):
            raise RuntimeError("friendship concurrency test timed out")
        return value


class _BlockingAccountRepository(DjangoAccountRepository):
    def __init__(self, locked: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self._locked = locked
        self._release = release

    def lock_owner_account(self):
        value = super().lock_owner_account()
        self._locked.set()
        if not self._release.wait(timeout=5):
            raise RuntimeError("account unlink concurrency test timed out")
        return value


class _NoCryptoExpected:
    def __getattr__(self, name):
        raise AssertionError(f"crypto must not be used: {name}")


class FriendshipProviderConcurrencyIntegrationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        runtime.load_credential_keyring()
        self.provider_id = "0012345678"
        self.subject = "U" + "a" * 32
        self.secret = "friendship-concurrency-secret"
        self.bot_user_id = "U" + "1" * 32
        self.identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=self.subject,
            display_name="Owner",
        )
        self.owner, _ = OwnerAccount.objects.get_or_create(slot=1)
        OwnerAccount.objects.filter(pk=self.owner.pk).update(
            state=OwnerAccount.State.ACTIVE,
            identity=self.identity,
        )
        self.channel = LineChannel.objects.create(
            messaging_api_channel_id=str(uuid4().int)[:20],
            bot_user_id=self.bot_user_id,
            label="競合対象チャネル",
            provider_id=self.provider_id,
            is_active=True,
        )
        cipher = FernetCredentialCipher(runtime.get_validated_keyring())
        access_token = cipher.encrypt(
            AccessToken("concurrency-access-token"),
            CredentialContext(self.channel.public_id, "access_token"),
        )
        channel_secret = cipher.encrypt(
            ChannelSecret(self.secret),
            CredentialContext(self.channel.public_id, "channel_secret"),
        )
        LineChannelCredential.objects.create(
            line_channel=self.channel,
            access_token_ciphertext=access_token.ciphertext,
            channel_secret_ciphertext=channel_secret.ciphertext,
        )
        self.recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.channel,
            enabled=False,
            friendship_state="unknown",
        )
        other_identity = LineIdentity.objects.create(
            provider_id="0099999999",
            subject=self.subject,
            display_name="Other provider",
        )
        self.other_recipient = DeliveryRecipient.objects.create(
            identity=other_identity,
            line_channel=self.channel,
            enabled=True,
            friendship_state="not_friend",
        )

    def _signed_follow(self, event_id: str) -> tuple[bytes, str, int]:
        occurred_at_ms = int(self.recipient.created_at.timestamp() * 1000) + 1
        raw_body, signature = self._signed_event(
            event_id,
            event_type="follow",
            occurred_at_ms=occurred_at_ms,
            is_redelivery=False,
        )
        return raw_body, signature, occurred_at_ms

    def _signed_event(
        self,
        event_id: str,
        *,
        event_type: str,
        occurred_at_ms: int,
        is_redelivery: bool,
    ) -> tuple[bytes, str]:
        raw_body = json.dumps(
            {
                "destination": self.bot_user_id,
                "events": [
                    {
                        "webhookEventId": event_id,
                        "type": event_type,
                        "timestamp": occurred_at_ms,
                        "deliveryContext": {"isRedelivery": is_redelivery},
                        "source": {"type": "user", "userId": self.subject},
                    }
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        signature = base64.b64encode(
            hmac.new(self.secret.encode(), raw_body, hashlib.sha256).digest()
        ).decode("ascii")
        return raw_body, signature

    def _provider_update(self, repository):
        return DefaultLineChannelService(
            repository,
            _NoCryptoExpected(),  # type: ignore[arg-type]
        ).update(
            UpdateLineChannel(
                self.channel.public_id,
                provider_id="0088888888",
                label="変更してはいけない",
                is_active=False,
            )
        )

    def _reset_projection(self) -> None:
        DeliveryRecipient.objects.filter(pk=self.recipient.pk).update(
            friendship_state="unknown",
            last_friendship_event_occurred_at_ms=None,
            last_friendship_webhook_event_id=None,
        )
        FriendshipSyncAudit.objects.all().delete()
        WebhookEventReceipt.objects.all().delete()

    def _run_concurrent_events(
        self,
        first: tuple[str, str, int, bool],
        second: tuple[str, str, int, bool],
    ) -> tuple[str, int | None, str | None]:
        start = threading.Barrier(2)
        services = (build_webhook_ingress_service(), build_webhook_ingress_service())
        payloads = tuple(
            self._signed_event(
                event_id,
                event_type=event_type,
                occurred_at_ms=occurred_at_ms,
                is_redelivery=is_redelivery,
            )
            for event_id, event_type, occurred_at_ms, is_redelivery in (first, second)
        )

        def ingest(service, payload):
            start.wait(timeout=5)
            body, signature = payload
            return service.ingest(str(self.channel.public_id), body, signature)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(
                executor.submit(
                    _with_independent_connection,
                    lambda service=service, payload=payload: ingest(service, payload),
                )
                for service, payload in zip(services, payloads, strict=True)
            )
            results = tuple(future.result(timeout=10) for future in futures)

        self.assertTrue(
            all(isinstance(result, IngressAccepted) for result in results)
        )
        receipts = dict(
            WebhookEventReceipt.objects.values_list("webhook_event_id", "status")
        )
        self.assertEqual(len(receipts), 2)
        self.assertEqual(
            receipts,
            {first[0]: "processed", second[0]: "processed"},
        )
        self.recipient.refresh_from_db()
        return (
            self.recipient.friendship_state,
            self.recipient.last_friendship_event_occurred_at_ms,
            self.recipient.last_friendship_webhook_event_id,
        )

    # テストケース: 異provider update先行とWebhook先行の両順序を独立connectionで競合させる
    # 期待値: provider変更だけを拒否し、元providerのexact recipientだけを更新して有限時間で完了する
    def test_provider_update_and_signed_webhook_converge_safely_in_both_orders(
        self,
    ) -> None:
        other_snapshot = (
            self.other_recipient.friendship_state,
            self.other_recipient.enabled,
            self.other_recipient.last_friendship_event_occurred_at_ms,
            self.other_recipient.last_friendship_webhook_event_id,
        )
        channel_locked = threading.Event()
        release_channel = threading.Event()
        webhook_started = threading.Event()
        first_service = build_webhook_ingress_service()
        first_body, first_signature, first_time = self._signed_follow(
            "01ARZ3NDEKTSV4RRFFQ69G5FBA"
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            update_future = executor.submit(
                _with_independent_connection,
                lambda: self._provider_update(
                    _BlockingLineChannelRepository(channel_locked, release_channel)
                ),
            )
            self.assertTrue(channel_locked.wait(timeout=5))
            webhook_future = executor.submit(
                _with_independent_connection,
                lambda: (
                    webhook_started.set(),
                    first_service.ingest(
                        str(self.channel.public_id),
                        first_body,
                        first_signature,
                    ),
                )[1],
            )
            self.assertTrue(webhook_started.wait(timeout=5))
            release_channel.set()
            update_result = update_future.result(timeout=5)
            webhook_result = webhook_future.result(timeout=5)

        self.assertIsInstance(webhook_result, IngressAccepted)
        self.assertEqual(
            (update_result.status, update_result.code),
            ("failed", "invalid_transition"),
        )
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.friendship_state, "friend")
        self.assertEqual(
            self.recipient.last_friendship_event_occurred_at_ms,
            first_time,
        )
        self.assertEqual(
            self.recipient.last_friendship_webhook_event_id,
            "01ARZ3NDEKTSV4RRFFQ69G5FBA",
        )
        self.other_recipient.refresh_from_db()
        self.assertEqual(
            (
                self.other_recipient.friendship_state,
                self.other_recipient.enabled,
                self.other_recipient.last_friendship_event_occurred_at_ms,
                self.other_recipient.last_friendship_webhook_event_id,
            ),
            other_snapshot,
        )

        self._reset_projection()
        account_locked = threading.Event()
        release_account = threading.Event()
        update_started = threading.Event()
        second_service = build_webhook_ingress_service()
        second_registration = second_service._registry.resolve("follow")
        assert second_registration is not None
        second_handler = second_registration.handler
        self.assertIsInstance(second_handler, DefaultFriendshipSyncService)
        assert isinstance(second_handler, DefaultFriendshipSyncService)
        second_handler.account_repository = _BlockingAccountProjectionRepository(
            account_locked,
            release_account,
        )
        second_body, second_signature, second_time = self._signed_follow(
            "01ARZ3NDEKTSV4RRFFQ69G5FBB"
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            webhook_future = executor.submit(
                _with_independent_connection,
                lambda: second_service.ingest(
                    str(self.channel.public_id),
                    second_body,
                    second_signature,
                ),
            )
            self.assertTrue(account_locked.wait(timeout=5))
            update_future = executor.submit(
                _with_independent_connection,
                lambda: (
                    update_started.set(),
                    self._provider_update(DjangoLineChannelRepository()),
                )[1],
            )
            self.assertTrue(update_started.wait(timeout=5))
            release_account.set()
            update_result = update_future.result(timeout=5)
            webhook_result = webhook_future.result(timeout=5)

        self.assertIsInstance(webhook_result, IngressAccepted)
        self.assertEqual(
            (update_result.status, update_result.code),
            ("failed", "invalid_transition"),
        )
        self.channel.refresh_from_db()
        self.recipient.refresh_from_db()
        self.other_recipient.refresh_from_db()
        self.assertEqual(self.channel.provider_id, self.provider_id)
        self.assertEqual(self.channel.label, "競合対象チャネル")
        self.assertTrue(self.channel.is_active)
        self.assertEqual(self.recipient.friendship_state, "friend")
        self.assertEqual(
            self.recipient.last_friendship_event_occurred_at_ms,
            second_time,
        )
        self.assertEqual(
            self.recipient.last_friendship_webhook_event_id,
            "01ARZ3NDEKTSV4RRFFQ69G5FBB",
        )
        self.assertFalse(self.recipient.enabled)
        self.assertEqual(
            (
                self.other_recipient.friendship_state,
                self.other_recipient.enabled,
                self.other_recipient.last_friendship_event_occurred_at_ms,
                self.other_recipient.last_friendship_webhook_event_id,
            ),
            other_snapshot,
        )

    # テストケース: 時刻差eventと同時刻の反対eventを独立connectionで同時処理する
    # 期待値: 開始順やisRedeliveryに依存せず最大(order timestamp, event ID)へ有限時間で収束する
    def test_concurrent_events_converge_to_maximum_order_key(self) -> None:
        baseline_ms = int(self.recipient.created_at.timestamp() * 1000)
        older = ("01ARZ3NDEKTSV4RRFFQ69G5FBC", "follow", baseline_ms + 10, False)
        newer = (
            "01ARZ3NDEKTSV4RRFFQ69G5FBD",
            "unfollow",
            baseline_ms + 20,
            True,
        )
        self.assertEqual(
            self._run_concurrent_events(older, newer),
            ("not_friend", baseline_ms + 20, "01ARZ3NDEKTSV4RRFFQ69G5FBD"),
        )

        self._reset_projection()
        same_time = baseline_ms + 30
        lower_id = (
            "01ARZ3NDEKTSV4RRFFQ69G5FBE",
            "follow",
            same_time,
            False,
        )
        higher_id = (
            "01ARZ3NDEKTSV4RRFFQ69G5FBF",
            "unfollow",
            same_time,
            True,
        )
        expected = (
            "not_friend",
            same_time,
            "01ARZ3NDEKTSV4RRFFQ69G5FBF",
        )
        self.assertEqual(
            self._run_concurrent_events(lower_id, higher_id),
            expected,
        )

        self._reset_projection()
        lower_redelivery = (*lower_id[:3], True)
        higher_redelivery = (*higher_id[:3], False)
        self.assertEqual(
            self._run_concurrent_events(higher_redelivery, lower_redelivery),
            expected,
        )

    # テストケース: friendship更新を個別解除・全解除finalizeと独立connectionで競合させ、後から再登録する
    # 期待値: 解除済みaggregateを復元せず、新しい登録境界後のeventだけを適用して有限時間で完了する
    def test_unlink_races_preserve_deletion_and_reregistration_boundary(self) -> None:
        event_locked = threading.Event()
        release_event = threading.Event()
        event_service = build_webhook_ingress_service()
        event_registration = event_service._registry.resolve("follow")
        assert event_registration is not None
        event_handler = event_registration.handler
        self.assertIsInstance(event_handler, DefaultFriendshipSyncService)
        assert isinstance(event_handler, DefaultFriendshipSyncService)
        event_handler.account_repository = _BlockingAccountProjectionRepository(
            event_locked,
            release_event,
        )
        first_event_time = int(self.recipient.created_at.timestamp() * 1000) + 1
        first_body, first_signature = self._signed_event(
            "01ARZ3NDEKTSV4RRFFQ69G5FBG",
            event_type="follow",
            occurred_at_ms=first_event_time,
            is_redelivery=False,
        )
        unlink_service = DefaultRecipientService(
            DjangoLineChannelDirectory(),
            DjangoAccountRepository(),
        )
        unlink_started = threading.Event()

        with ThreadPoolExecutor(max_workers=2) as executor:
            webhook_future = executor.submit(
                _with_independent_connection,
                lambda: event_service.ingest(
                    str(self.channel.public_id), first_body, first_signature
                ),
            )
            self.assertTrue(event_locked.wait(timeout=5))
            unlink_future = executor.submit(
                _with_independent_connection,
                lambda: (
                    unlink_started.set(),
                    unlink_service.unlink(
                        self.identity.public_id,
                        self.recipient.public_id,
                    ),
                )[1],
            )
            self.assertTrue(unlink_started.wait(timeout=5))
            release_event.set()
            webhook_result = webhook_future.result(timeout=10)
            unlink_result = unlink_future.result(timeout=10)

        self.assertIsInstance(webhook_result, IngressAccepted)
        self.assertIsInstance(unlink_result, RecipientMutationSucceeded)
        self.assertFalse(
            DeliveryRecipient.objects.filter(public_id=self.recipient.public_id).exists()
        )
        self.assertTrue(LineIdentity.objects.filter(pk=self.identity.pk).exists())
        self.assertEqual(
            FriendshipSyncAudit.objects.get(
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FBG"
            ).outcome,
            "applied",
        )

        finalized_recipient = DeliveryRecipient.objects.create(
            identity=self.identity,
            line_channel=self.channel,
            enabled=True,
            friendship_state="unknown",
        )
        session = OwnerSession.objects.create(
            owner_id=1,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        generation = uuid4()
        OwnerAccount.objects.filter(slot=1).update(
            state=OwnerAccount.State.LOCAL_DELETION_PENDING,
            identity=self.identity,
            unlink_generation=generation,
            line_deauthorized_at=timezone.now(),
        )
        finalize_locked = threading.Event()
        release_finalize = threading.Event()
        finalize_repository = _BlockingAccountRepository(
            finalize_locked,
            release_finalize,
        )

        def finalize_unlink() -> None:
            with transaction.atomic():
                owner = finalize_repository.lock_owner_account()
                finalize_repository.finalize_unlink(owner, generation)

        second_event_time = int(finalized_recipient.created_at.timestamp() * 1000) + 1
        second_body, second_signature = self._signed_event(
            "01ARZ3NDEKTSV4RRFFQ69G5FBH",
            event_type="follow",
            occurred_at_ms=second_event_time,
            is_redelivery=False,
        )
        second_service = build_webhook_ingress_service()
        second_started = threading.Event()

        with ThreadPoolExecutor(max_workers=2) as executor:
            finalize_future = executor.submit(
                _with_independent_connection,
                finalize_unlink,
            )
            self.assertTrue(finalize_locked.wait(timeout=5))
            webhook_future = executor.submit(
                _with_independent_connection,
                lambda: (
                    second_started.set(),
                    second_service.ingest(
                        str(self.channel.public_id),
                        second_body,
                        second_signature,
                    ),
                )[1],
            )
            self.assertTrue(second_started.wait(timeout=5))
            release_finalize.set()
            finalize_future.result(timeout=10)
            webhook_result = webhook_future.result(timeout=10)

        self.assertIsInstance(webhook_result, IngressAccepted)
        owner = OwnerAccount.objects.get(slot=1)
        self.assertEqual(owner.state, OwnerAccount.State.VACANT)
        self.assertIsNone(owner.identity_id)
        self.assertFalse(LineIdentity.objects.filter(pk=self.identity.pk).exists())
        self.assertFalse(
            DeliveryRecipient.objects.filter(
                public_id=finalized_recipient.public_id
            ).exists()
        )
        self.assertTrue(
            DeliveryRecipient.objects.filter(
                public_id=self.other_recipient.public_id,
                friendship_state="not_friend",
                enabled=True,
            ).exists()
        )
        self.assertFalse(OwnerSession.objects.filter(pk=session.pk).exists())
        self.assertEqual(
            FriendshipSyncAudit.objects.get(
                webhook_event_id="01ARZ3NDEKTSV4RRFFQ69G5FBH"
            ).outcome,
            "unlinked",
        )

        reregistered_identity = LineIdentity.objects.create(
            provider_id=self.provider_id,
            subject=self.subject,
            display_name="Re-registered owner",
        )
        OwnerAccount.objects.filter(slot=1).update(
            state=OwnerAccount.State.ACTIVE,
            identity=reregistered_identity,
            unlink_generation=None,
            line_deauthorized_at=None,
        )
        reregistered = DeliveryRecipient.objects.create(
            identity=reregistered_identity,
            line_channel=self.channel,
            enabled=False,
            friendship_state="unknown",
        )
        old_body, old_signature = self._signed_event(
            "01ARZ3NDEKTSV4RRFFQ69G5FBJ",
            event_type="follow",
            occurred_at_ms=0,
            is_redelivery=True,
        )
        new_time = int(reregistered.created_at.timestamp() * 1000) + 1
        new_body, new_signature = self._signed_event(
            "01ARZ3NDEKTSV4RRFFQ69G5FBK",
            event_type="follow",
            occurred_at_ms=new_time,
            is_redelivery=False,
        )
        service = build_webhook_ingress_service()

        old_result = service.ingest(
            str(self.channel.public_id), old_body, old_signature
        )
        new_result = service.ingest(
            str(self.channel.public_id), new_body, new_signature
        )

        self.assertIsInstance(old_result, IngressAccepted)
        self.assertIsInstance(new_result, IngressAccepted)
        reregistered.refresh_from_db()
        self.assertEqual(reregistered.friendship_state, "friend")
        self.assertFalse(reregistered.enabled)
        self.assertEqual(
            reregistered.last_friendship_event_occurred_at_ms,
            new_time,
        )
        self.assertEqual(
            reregistered.last_friendship_webhook_event_id,
            "01ARZ3NDEKTSV4RRFFQ69G5FBK",
        )
        self.assertEqual(
            list(
                FriendshipSyncAudit.objects.filter(
                    webhook_event_id__in=(
                        "01ARZ3NDEKTSV4RRFFQ69G5FBJ",
                        "01ARZ3NDEKTSV4RRFFQ69G5FBK",
                    )
                )
                .order_by("pk")
                .values_list("outcome", flat=True)
            ),
            ["stale", "applied"],
        )
        self.assertTrue(
            LineIdentity.objects.filter(pk=reregistered_identity.pk).exists()
        )
        self.assertEqual(
            DeliveryRecipient.objects.filter(
                identity=reregistered_identity,
                line_channel=self.channel,
            ).count(),
            1,
        )
        self.assertTrue(
            DeliveryRecipient.objects.filter(
                public_id=self.other_recipient.public_id,
                friendship_state="not_friend",
                enabled=True,
            ).exists()
        )
        self.assertEqual(OwnerSession.objects.count(), 0)
