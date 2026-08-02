import json
import logging
import pickle
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from django.test import SimpleTestCase

from linechannels.types import AccessToken, ChannelSecret
from linerichmenus.gateway import (
    CreateAccepted,
    RichMenuArea,
    RichMenuBounds,
    RichMenuGatewayContext,
    RichMenuObject,
    RichMenuUriAction,
    ResourceListAccepted,
    ResourceSummary,
)
from linerichmenus.presenters import RichMenuPresenter
from linerichmenus.services import HistorySucceeded, ServiceFailed
from linerichmenus.types import (
    CleanupRelation,
    DefaultRelation,
    HistoryEntry,
    HistoryPage,
    InputFieldError,
    IssuedConfirmation,
    NextAllowedAction,
    NormalizedTemplate,
    OperationKind,
    OperationCommand,
    OperationStatus,
    OperationView,
    SafeResultCode,
    PreviewSnapshot,
    RenderedImage,
    TemplateFieldValue,
    TemplateReference,
)


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
ACCESS_TOKEN = "access-token-security-canary"
FULL_URL = "https://example.com/security-canary?secret=must-not-leak"
LINE_ID = "rich-menu-line-id-security-canary"
RAW_BODY = "raw-line-response-security-canary"
CHANNEL_SECRET = "channel-secret-security-canary"
CONFIRMATION_TOKEN = "confirmation-token-security-canary"
IMAGE_BINARY = b"image-binary-security-canary"


class RichMenuCrossBoundarySecurityTests(SimpleTestCase):
    # テストケース: gateway call scopeとLINE resource値へ秘密canaryを注入する。
    # 期待値: repr・str・pickle・JSON境界からtoken、URL、LINE IDを露出しない。
    def test_gateway_values_redact_and_disable_generic_serialization(self):
        context = RichMenuGatewayContext(
            channel_public_id=uuid4(),
            channel_revision=NOW,
            access_token=AccessToken(ACCESS_TOKEN),
        )
        action = RichMenuUriAction(FULL_URL)
        menu = RichMenuObject(
            width=800,
            height=550,
            name="ownership-marker-security-canary",
            chat_bar_text="表示-security-canary",
            areas=(RichMenuArea(RichMenuBounds(0, 0, 800, 550), action),),
        )
        values = (
            context,
            action,
            menu,
            CreateAccepted(LINE_ID),
            ResourceSummary(LINE_ID, "ownership-marker-security-canary"),
            ResourceListAccepted(
                (ResourceSummary(LINE_ID, "ownership-marker-security-canary"),)
            ),
        )

        rendered = " ".join(f"{value!r} {value!s}" for value in values)
        for canary in (ACCESS_TOKEN, FULL_URL, LINE_ID, "ownership-marker-security-canary"):
            self.assertNotIn(canary, rendered)
        for value in values:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    pickle.dumps(value)
                with self.assertRaises(TypeError):
                    json.dumps(value)

    # テストケース: untrusted例外、履歴、operation responseへ禁止canaryを注入する。
    # 期待値: error/operationは安全分類だけを返し、完全URLはowner履歴だけに残る。
    def test_presenter_exposes_full_url_only_in_owner_history(self):
        presenter = RichMenuPresenter()
        operation = OperationView(
            operation_id=uuid4(),
            kind=OperationKind.APPLY,
            status=OperationStatus.SUCCEEDED,
            stage=None,
            result=SafeResultCode.SUCCEEDED,
            subject_operation_id=None,
            target_resource_id=None,
            accepted_at=NOW,
            completed_at=NOW,
            next_allowed_actions=(NextAllowedAction.VIEW_HISTORY,),
        )
        history = HistorySucceeded(
            HistoryPage(
                entries=(
                    HistoryEntry(
                        operation=operation,
                        channel_public_id=uuid4(),
                        channel_label="履歴-security-canary",
                        configuration=NormalizedTemplate(
                            TemplateReference("jp-link-one", 1),
                            (TemplateFieldValue("表示-security-canary", FULL_URL),),
                        ),
                        transitions=(SafeResultCode.SUCCEEDED,),
                        default_relation=DefaultRelation.BECAME_DEFAULT,
                        cleanup_relation=CleanupRelation.NOT_REQUIRED,
                    ),
                ),
                next_cursor=None,
                has_more=False,
            )
        )
        failure = ServiceFailed(
            SafeResultCode.RESPONSE_UNKNOWN,
            errors=(InputFieldError(RAW_BODY, "invalid"),),
        )

        operation_body = presenter.operation(operation)
        error_body = presenter.error(
            failure,
            error=RuntimeError(f"{ACCESS_TOKEN} {FULL_URL} {LINE_ID} {RAW_BODY}"),
        )
        history_body = presenter.history(history)

        non_history = json.dumps(
            {"operation": operation_body, "error": error_body}, ensure_ascii=False
        )
        for canary in (ACCESS_TOKEN, FULL_URL, LINE_ID, RAW_BODY):
            self.assertNotIn(canary, non_history)
        history_json = json.dumps(history_body, ensure_ascii=False)
        self.assertIn(FULL_URL, history_json)
        self.assertNotIn(ACCESS_TOKEN, history_json)
        self.assertNotIn(LINE_ID, history_json)
        self.assertNotIn(RAW_BODY, history_json)

    # テストケース: owner identity、channel secret、確認token、画像binaryをrepr/error/log境界へ注入する。
    # 期待値: preview/historyで明示許可された値以外は全境界でredactされ、通常logへも出ない。
    def test_all_sensitive_canaries_are_redacted_from_repr_error_and_logs(self):
        owner_identity = uuid4()
        template = NormalizedTemplate(
            TemplateReference("jp-link-one", 1),
            (TemplateFieldValue("表示-security-canary", FULL_URL),),
        )
        values = (
            ChannelSecret(CHANNEL_SECRET),
            IssuedConfirmation(CONFIRMATION_TOKEN, NOW, "c" * 64),
            RenderedImage("image/png", 800, 550, "d" * 64, IMAGE_BINARY),
            PreviewSnapshot(
                owner_identity=owner_identity,
                provider_id="provider-security-canary",
                channel_public_id=uuid4(),
                channel_revision=NOW,
                default_observation_fingerprint="e" * 64,
                template=template,
                pixel_digest="d" * 64,
            ),
            OperationCommand(
                operation_id=uuid4(),
                channel_public_id=uuid4(),
                expected_channel_revision=NOW,
                kind=OperationKind.APPLY,
                subject_operation_id=None,
                target_resource_id=None,
                confirmation_token=CONFIRMATION_TOKEN,
                template=template,
            ),
        )
        rendered = " ".join(repr(value) for value in values)
        canaries = (
            str(owner_identity),
            CHANNEL_SECRET,
            CONFIRMATION_TOKEN,
            IMAGE_BINARY.decode(),
            "provider-security-canary",
            FULL_URL,
        )
        for canary in canaries:
            self.assertNotIn(canary, rendered)

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = Capture()
        logger = logging.getLogger("linerichmenus.security-test")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            logger.info("safe rich-menu result: %r", values)
        finally:
            logger.removeHandler(handler)
        logged = " ".join(records)
        for canary in canaries:
            self.assertNotIn(canary, logged)

        malicious_operation = SimpleNamespace(
            operation_id=uuid4(),
            kind=OperationKind.APPLY,
            status=OperationStatus.SUCCEEDED,
            stage=None,
            result=SafeResultCode.SUCCEEDED,
            subject_operation_id=None,
            target_resource_id=None,
            accepted_at=NOW,
            completed_at=NOW,
            next_allowed_actions=(),
            owner_identity=owner_identity,
            channel_secret=CHANNEL_SECRET,
            confirmation_token=CONFIRMATION_TOKEN,
            image_binary=IMAGE_BINARY,
        )
        malicious_state = SimpleNamespace(
            channel_public_id=uuid4(),
            current_resource=None,
            blocking_operation=malicious_operation,
            active_operation=None,
            cleanup_resources=(),
            latest_observation=None,
            history_summary=SimpleNamespace(
                total_count=1,
                latest_operation_id=malicious_operation.operation_id,
                latest_status=OperationStatus.SUCCEEDED,
            ),
            next_allowed_actions=(),
            owner_identity=owner_identity,
            channel_secret=CHANNEL_SECRET,
            confirmation_token=CONFIRMATION_TOKEN,
            image_binary=IMAGE_BINARY,
        )
        malicious_entry = SimpleNamespace(
            operation=malicious_operation,
            channel_public_id=uuid4(),
            channel_label="安全な履歴表示",
            configuration=None,
            transitions=(SafeResultCode.SUCCEEDED,),
            default_relation=DefaultRelation.BECAME_DEFAULT,
            cleanup_relation=CleanupRelation.NOT_REQUIRED,
            owner_identity=owner_identity,
            channel_secret=CHANNEL_SECRET,
            confirmation_token=CONFIRMATION_TOKEN,
            image_binary=IMAGE_BINARY,
        )
        presenter = RichMenuPresenter()
        bodies = {
            "operation": presenter.operation(malicious_operation),
            "state": presenter.state(SimpleNamespace(state=malicious_state)),
            "history": presenter.history(
                SimpleNamespace(
                    history=SimpleNamespace(
                        entries=(malicious_entry,), next_cursor=None, has_more=False
                    )
                )
            ),
            "error": presenter.error(
                ServiceFailed(SafeResultCode.RESPONSE_UNKNOWN),
                error=RuntimeError(" ".join(canaries)),
            ),
        }
        serialized = json.dumps(bodies, ensure_ascii=False)
        for canary in canaries:
            self.assertNotIn(canary, serialized)
