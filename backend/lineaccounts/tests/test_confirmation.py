from datetime import timedelta
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone

from lineaccounts.confirmation import UnlinkConfirmation
from lineaccounts.repositories import UnlinkSnapshot


class UnlinkConfirmationTests(SimpleTestCase):
    def snapshot(self, *, display_name="Owner"):
        recipient_ids = (uuid4(), uuid4())
        channel_ids = (uuid4(), uuid4())
        return UnlinkSnapshot(
            owner_slot=1,
            identity_id=uuid4(),
            display_name=display_name,
            recipient_ids=tuple(reversed(recipient_ids)),
            channel_ids=tuple(reversed(channel_ids)),
        )

    # テストケース: 同じ解除snapshotを異なる入力順で署名・検証する
    # 期待値: canonical fingerprintが一致し5分以内だけ検証に成功する
    def test_signs_and_verifies_canonical_snapshot_for_five_minutes(self):
        now = timezone.now()
        snapshot = self.snapshot()
        canonical = UnlinkSnapshot(
            owner_slot=snapshot.owner_slot,
            identity_id=snapshot.identity_id,
            display_name=snapshot.display_name,
            recipient_ids=tuple(reversed(snapshot.recipient_ids)),
            channel_ids=tuple(reversed(snapshot.channel_ids)),
        )
        confirmation = UnlinkConfirmation()

        token = confirmation.issue(snapshot, now)

        self.assertTrue(confirmation.verify(token, canonical, now + timedelta(minutes=4)))
        self.assertFalse(confirmation.verify(token, canonical, now + timedelta(minutes=5, seconds=1)))

    # テストケース: 改変tokenまたはsnapshot変更後のtokenを検証する
    # 期待値: 秘密値を返さずstale confirmationとして拒否できるFalseになる
    def test_rejects_tampering_and_snapshot_changes(self):
        now = timezone.now()
        snapshot = self.snapshot()
        confirmation = UnlinkConfirmation()
        token = confirmation.issue(snapshot, now)

        self.assertFalse(confirmation.verify(f"{token}x", snapshot, now))
        self.assertFalse(
            confirmation.verify(
                token,
                self.snapshot(display_name="Changed"),
                now,
            )
        )
