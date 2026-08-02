from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.core import signing
from django.test import SimpleTestCase

from linerichmenus.confirmation import DefaultRichMenuConfirmation
from linerichmenus.types import (
    ConfirmationAccepted,
    ConfirmationRejected,
    NormalizedTemplate,
    PreviewSnapshot,
    TemplateFieldValue,
    TemplateReference,
)


class RichMenuConfirmationTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 2, 3, 0, tzinfo=UTC)
        self.confirmation = DefaultRichMenuConfirmation()
        self.snapshot = PreviewSnapshot(
            owner_identity=UUID("00000000-0000-4000-8000-000000000001"),
            provider_id="0012345678",
            channel_public_id=UUID("00000000-0000-4000-8000-000000000002"),
            channel_revision=datetime(2026, 8, 2, 2, 55, tzinfo=UTC),
            default_observation_fingerprint="a" * 64,
            template=NormalizedTemplate(
                reference=TemplateReference("jp-link-one", 1),
                fields=(
                    TemplateFieldValue(
                        "秘密表示名", "https://example.com/private?token=url-canary"
                    ),
                ),
            ),
            pixel_digest="b" * 64,
        )

    # テストケース: preview snapshotを発行直後と10分境界で検証する。
    # 期待値: 完全一致snapshotだけが同じusage digestで受理される。
    def test_accepts_exact_snapshot_through_ten_minute_boundary(self):
        issued = self.confirmation.issue(self.snapshot, self.now)

        immediate = self.confirmation.verify(issued.token, self.snapshot, self.now)
        boundary = self.confirmation.verify(
            issued.token, self.snapshot, self.now + timedelta(minutes=10)
        )

        self.assertIsInstance(immediate, ConfirmationAccepted)
        self.assertEqual(immediate.usage_digest, issued.usage_digest)
        self.assertEqual(boundary, immediate)
        self.assertEqual(issued.expires_at, self.now + timedelta(minutes=10))

    # テストケース: 10分超過、未来発行、token改変を検証する。
    # 期待値: 保存状態を変えられる情報を返さず新preview要求へ拒否する。
    def test_rejects_expired_future_and_tampered_tokens(self):
        issued = self.confirmation.issue(self.snapshot, self.now)
        future = self.confirmation.issue(self.snapshot, self.now + timedelta(seconds=1))
        tampered = issued.token[:-1] + ("A" if issued.token[-1] != "A" else "B")

        self.assertEqual(
            self.confirmation.verify(
                issued.token, self.snapshot, self.now + timedelta(minutes=10, microseconds=1)
            ),
            ConfirmationRejected(reason="preview_expired"),
        )
        self.assertEqual(
            self.confirmation.verify(future.token, self.snapshot, self.now),
            ConfirmationRejected(reason="preview_invalid"),
        )
        self.assertEqual(
            self.confirmation.verify(tampered, self.snapshot, self.now),
            ConfirmationRejected(reason="preview_invalid"),
        )

    # テストケース: snapshotの各binding軸を一つずつ変更する。
    # 期待値: 以前のtokenを全て拒否して最新previewを要求する。
    def test_rejects_every_changed_snapshot_axis(self):
        issued = self.confirmation.issue(self.snapshot, self.now)
        changed_template = NormalizedTemplate(
            reference=self.snapshot.template.reference,
            fields=(TemplateFieldValue("別表示", "https://example.com/other"),),
        )
        variants = (
            replace(self.snapshot, owner_identity=UUID("00000000-0000-4000-8000-000000000011")),
            replace(self.snapshot, provider_id="0099999999"),
            replace(self.snapshot, channel_public_id=UUID("00000000-0000-4000-8000-000000000012")),
            replace(self.snapshot, channel_revision=self.snapshot.channel_revision + timedelta(seconds=1)),
            replace(self.snapshot, default_observation_fingerprint="c" * 64),
            replace(self.snapshot, template=changed_template),
            replace(
                self.snapshot,
                template=replace(
                    self.snapshot.template,
                    reference=TemplateReference("jp-link-one", 2),
                ),
            ),
            replace(self.snapshot, pixel_digest="d" * 64),
        )
        for changed in variants:
            with self.subTest(changed=repr(changed)):
                self.assertEqual(
                    self.confirmation.verify(issued.token, changed, self.now),
                    ConfirmationRejected(reason="preview_changed"),
                )

    # テストケース: 同じsnapshotから二つの確認値を発行する。
    # 期待値: random nonceによりtokenと再利用防止usage keyが異なる。
    def test_new_nonce_allows_distinct_confirmation_for_same_snapshot(self):
        first = self.confirmation.issue(self.snapshot, self.now)
        second = self.confirmation.issue(self.snapshot, self.now)

        self.assertNotEqual(first.token, second.token)
        self.assertNotEqual(first.usage_digest, second.usage_digest)
        self.assertIsInstance(
            self.confirmation.verify(first.token, self.snapshot, self.now),
            ConfirmationAccepted,
        )
        self.assertIsInstance(
            self.confirmation.verify(second.token, self.snapshot, self.now),
            ConfirmationAccepted,
        )

    # テストケース: tokenの署名済みpayloadを安全にdecodeして構造を調べる。
    # 期待値: purpose/version/time/nonce/fingerprint以外のsnapshot値を含まない。
    def test_token_payload_contains_only_safe_binding_fields(self):
        issued = self.confirmation.issue(self.snapshot, self.now)
        payload = signing.loads(
            issued.token,
            salt=DefaultRichMenuConfirmation.SALT,
        )
        serialized = repr(payload)

        self.assertEqual(
            set(payload),
            {"purpose", "version", "issuedAt", "nonce", "fingerprint"},
        )
        for canary in (
            "秘密表示名",
            "https://",
            "url-canary",
            str(self.snapshot.owner_identity),
            str(self.snapshot.channel_public_id),
            self.snapshot.provider_id,
            self.snapshot.pixel_digest,
        ):
            self.assertNotIn(canary, serialized)

    # テストケース: 同じsnapshotのtokenを別operation purposeで検証する。
    # 期待値: 署名が正しくても別用途利用を拒否する。
    def test_rejects_token_issued_for_another_purpose(self):
        other = DefaultRichMenuConfirmation(purpose="cleanup")
        issued = other.issue(self.snapshot, self.now)

        self.assertEqual(
            self.confirmation.verify(issued.token, self.snapshot, self.now),
            ConfirmationRejected(reason="preview_invalid"),
        )

    # テストケース: snapshot、発行結果、検証結果のreprを生成する。
    # 期待値: URL、表示名、token、内部IDをデバッグ表現へ露出しない。
    def test_confirmation_values_redact_sensitive_snapshot_data(self):
        issued = self.confirmation.issue(self.snapshot, self.now)
        accepted = self.confirmation.verify(issued.token, self.snapshot, self.now)

        rendered = repr((self.snapshot, issued, accepted))
        for canary in ("秘密表示名", "https://", issued.token, str(self.snapshot.channel_public_id)):
            self.assertNotIn(canary, rendered)
