import pickle
from datetime import datetime, timezone
from uuid import uuid4

from django.test import SimpleTestCase

from linechannels.admin_types import (
    AdminConnectionSnapshot,
    AdminServiceFailed,
    ChannelDeleteSucceeded,
    ConnectionCheckCompleted,
    RegisterAdminChannel,
    SetAdminChannelState,
    SnapshotAvailable,
    UpdateAdminChannel,
)
from linechannels.types import AccessToken, ChannelSecret, CredentialPair


class AdminTypesTests(SimpleTestCase):
    # テストケース: write-only資格情報を持つ全管理commandとsnapshotを表示・直列化する
    # 期待値: access tokenとchannel secretを文字列表現へ含めずpickleも拒否する
    def test_write_only_commands_and_snapshot_never_render_or_serialize_secrets(self):
        access_canary = "access-token-canary"
        secret_canary = "channel-secret-canary"
        credentials = CredentialPair(
            AccessToken(access_canary),
            ChannelSecret(secret_canary),
        )
        now = datetime.now(timezone.utc)
        values = (
            RegisterAdminChannel(
                messaging_api_channel_id="123456",
                bot_user_id="U" + "1" * 32,
                label="管理チャネル",
                provider_id="000123",
                credentials=credentials,
                is_active=True,
            ),
            UpdateAdminChannel(
                channel_public_id=uuid4(),
                expected_updated_at=now,
                credentials=credentials,
            ),
            SetAdminChannelState(
                channel_public_id=uuid4(),
                expected_updated_at=now,
                is_active=True,
                repair_credentials=credentials,
            ),
            AdminConnectionSnapshot(
                access_token=AccessToken(access_canary),
                expected_bot_user_id="U" + "1" * 32,
                expected_updated_at=now,
            ),
            SnapshotAvailable(
                AdminConnectionSnapshot(
                    access_token=AccessToken(access_canary),
                    expected_bot_user_id="U" + "1" * 32,
                    expected_updated_at=now,
                )
            ),
        )

        for value in values:
            with self.subTest(value=type(value).__name__):
                rendered = f"{value!r} {value}"
                self.assertNotIn(access_canary, rendered)
                self.assertNotIn(secret_canary, rendered)
                with self.assertRaises(TypeError):
                    pickle.dumps(value)

    # テストケース: 接続確認、service失敗、削除成功の固定safe結果を生成する
    # 期待値: 6分類・限定scope・safe failure codeを受理し、不正分類とnaive時刻を拒否する
    def test_service_results_use_fixed_safe_classifications_and_scope(self):
        now = datetime.now(timezone.utc)
        statuses = (
            "connected",
            "credential_unavailable",
            "authentication_failed",
            "identity_mismatch",
            "rate_limited",
            "line_unavailable",
        )
        for status in statuses:
            with self.subTest(status=status):
                result = ConnectionCheckCompleted(status, now)
                self.assertEqual(result.status, status)
                self.assertEqual(result.scope, "access_token_and_bot_identity_only")

        failure_codes = (
            "provider_mismatch",
            "credential_unavailable",
            "channel_referenced",
            "storage_retryable",
        )
        self.assertEqual(
            tuple(AdminServiceFailed(code).code for code in failure_codes),
            failure_codes,
        )
        deleted = ChannelDeleteSucceeded(uuid4(), "削除済み")
        self.assertFalse(hasattr(deleted, "credentials"))
        with self.assertRaises(ValueError):
            ConnectionCheckCompleted("unknown", now)
        with self.assertRaises(ValueError):
            ConnectionCheckCompleted("connected", datetime.now())
