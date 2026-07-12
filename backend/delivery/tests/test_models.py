import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from delivery.models import DeliveryAttempt


class DeliveryAttemptTests(TestCase):
    def make_attempt(self, **overrides):
        accepted_at = timezone.now()
        values = {
            "operation_id": uuid.uuid4(),
            "subject": "件名",
            "body": "本文\n2行目",
            "formatted_text": "【件名】\n\n本文\n2行目",
            "content_fingerprint": "a" * 64,
            "active_content_fingerprint": "a" * 64,
            "accepted_at": accepted_at,
            "processing_expires_at": accepted_at + timedelta(seconds=30),
        }
        values.update(overrides)
        return DeliveryAttempt.objects.create(**values)

    # テストケース: 必須項目だけで処理中の送信試行を作成する。
    # 期待値: processingと固定宛先方式が設定され、文字列表現に入力内容が含まれない。
    def test_processing_attempt_has_safe_defaults(self):
        attempt = self.make_attempt()

        self.assertEqual(attempt.status, DeliveryAttempt.Status.PROCESSING)
        self.assertEqual(attempt.target_mode, DeliveryAttempt.TargetMode.FIXED_USER)
        self.assertNotIn(attempt.subject, str(attempt))
        self.assertNotIn(attempt.body, str(attempt))
        self.assertNotIn(attempt.formatted_text, str(attempt))

    # テストケース: 同じ操作IDを持つ送信試行を2件作成する。
    # 期待値: DBの一意制約により2件目の作成が拒否される。
    def test_operation_id_is_unique(self):
        attempt = self.make_attempt()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_attempt(
                operation_id=attempt.operation_id,
                content_fingerprint="b" * 64,
                active_content_fingerprint="b" * 64,
            )

    # テストケース: 同じ処理中content fingerprintを持つ別の送信試行を作成する。
    # 期待値: DBの一意制約により重複する処理中試行の作成が拒否される。
    def test_active_content_fingerprint_is_unique_while_processing(self):
        attempt = self.make_attempt()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_attempt(
                content_fingerprint=attempt.content_fingerprint,
                active_content_fingerprint=attempt.active_content_fingerprint,
            )

    # テストケース: processingの送信試行をLINE受付成功へ遷移させる。
    # 期待値: succeededの日時とrequest IDが保存され、処理中fingerprintが解放される。
    def test_succeeded_transition_releases_active_fingerprint(self):
        attempt = self.make_attempt()
        completed_at = timezone.now()

        attempt.mark_succeeded(
            completed_at=completed_at,
            line_request_id="request-id",
        )
        attempt.refresh_from_db()

        self.assertEqual(attempt.status, DeliveryAttempt.Status.SUCCEEDED)
        self.assertIsNone(attempt.active_content_fingerprint)
        self.assertEqual(attempt.sent_at, completed_at)
        self.assertEqual(attempt.completed_at, completed_at)
        self.assertEqual(attempt.line_request_id, "request-id")
        self.assertIsNone(attempt.failure_type)

    # テストケース: processingの送信試行をfailedおよびunknownへ遷移させる。
    # 期待値: 各状態の安全な失敗種別と完了日時が保存され、処理中fingerprintが解放される。
    def test_failed_and_unknown_transitions_record_safe_failure(self):
        for status, failure_type in (
            (DeliveryAttempt.Status.FAILED, DeliveryAttempt.FailureType.PERMISSION),
            (DeliveryAttempt.Status.UNKNOWN, DeliveryAttempt.FailureType.TIMEOUT_UNKNOWN),
        ):
            with self.subTest(status=status):
                attempt = self.make_attempt(
                    operation_id=uuid.uuid4(),
                    content_fingerprint=str(uuid.uuid4()).replace("-", "") * 2,
                    active_content_fingerprint=str(uuid.uuid4()).replace("-", "") * 2,
                )
                completed_at = timezone.now()

                attempt.mark_unsuccessful(
                    status=status,
                    failure_type=failure_type,
                    completed_at=completed_at,
                )
                attempt.refresh_from_db()

                self.assertEqual(attempt.status, status)
                self.assertIsNone(attempt.active_content_fingerprint)
                self.assertEqual(attempt.failed_at, completed_at)
                self.assertEqual(attempt.completed_at, completed_at)
                self.assertEqual(attempt.failure_type, failure_type)
                self.assertIsNone(attempt.sent_at)

    # テストケース: succeededへ遷移済みの送信試行をunknownへ再遷移させる。
    # 期待値: terminal状態からの再遷移がValidationErrorで拒否される。
    def test_terminal_attempt_rejects_another_transition(self):
        attempt = self.make_attempt()
        attempt.mark_succeeded(completed_at=timezone.now())

        with self.assertRaises(ValidationError):
            attempt.mark_unsuccessful(
                status=DeliveryAttempt.Status.UNKNOWN,
                failure_type=DeliveryAttempt.FailureType.PROCESSING_EXPIRED,
                completed_at=timezone.now(),
            )

    # テストケース: succeededに必須の日時を設定せず不正な状態組合せを保存する。
    # 期待値: DBの状態整合性制約により保存が拒否される。
    def test_database_rejects_invalid_state_field_combinations(self):
        attempt = self.make_attempt()
        attempt.status = DeliveryAttempt.Status.SUCCEEDED
        attempt.active_content_fingerprint = None

        with self.assertRaises(IntegrityError), transaction.atomic():
            attempt.save()

    # テストケース: DeliveryAttemptが保持する永続化フィールドを確認する。
    # 期待値: 監査必須項目を持ち、秘密値、宛先値、確認token、raw error用フィールドを持たない。
    def test_model_has_no_fields_for_secrets_target_or_raw_errors(self):
        field_names = {field.name for field in DeliveryAttempt._meta.fields}

        self.assertTrue(
            {
                "operation_id",
                "subject",
                "body",
                "formatted_text",
                "content_fingerprint",
                "target_mode",
                "line_request_id",
            }.issubset(field_names)
        )
        self.assertFalse(
            {
                "target",
                "line_user_id",
                "access_token",
                "channel_secret",
                "confirmation_token",
                "raw_error",
            }
            & field_names
        )
