from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from linerichmenus.models import (
    ManagedRichMenu,
    RichMenuChannelState,
    RichMenuOperation,
    RichMenuOperationTransition,
)


class RichMenuModelConstraintTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.state = RichMenuChannelState.objects.create(channel_public_id=uuid4())
        self.apply_operation = self._create_operation(kind="apply")
        self.resource = ManagedRichMenu.objects.create(
            channel_state=self.state,
            origin_operation=self.apply_operation,
            ownership_marker="marker-" + uuid4().hex,
            lifecycle="candidate",
            image_digest="a" * 64,
        )

    # テストケース: operation kindごとのsubject/target relationをDBへ保存する。
    # 期待値: apply・unlink・release・recheck・cleanupの正しいrelationだけが受理される。
    def test_operation_relations_accept_only_valid_shapes(self):
        recheck = self._create_operation(
            kind="recheck", subject_operation=self.apply_operation
        )
        self._create_operation(kind="unlink", target_resource=self.resource)
        self._create_operation(kind="release", target_resource=self.resource)
        self._create_operation(
            kind="cleanup",
            subject_operation=recheck,
            target_resource=self.resource,
        )

        invalid_cases = (
            {"kind": "apply", "subject_operation": self.apply_operation},
            {"kind": "unlink"},
            {"kind": "recheck", "target_resource": self.resource},
            {"kind": "cleanup", "subject_operation": recheck},
        )
        for values in invalid_cases:
            with self.subTest(values=values), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._create_operation(**values)

    # テストケース: operationの未定義status/stageをDBへ保存する。
    # 期待値: choices外のstageをCHECK制約が拒否する。
    def test_operation_stage_is_constrained(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create_operation(
                    kind="apply", status="processing", stage="unexpected"
                )

    # テストケース: resource lifecycleとLINE ID・ownership markerの一意性をDBで検証する。
    # 期待値: 未定義lifecycleと重複識別値がCHECK/UNIQUE制約で拒否される。
    def test_resource_lifecycle_and_identifiers_are_constrained(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ManagedRichMenu.objects.create(
                    channel_state=self.state,
                    origin_operation=self.apply_operation,
                    ownership_marker="invalid-lifecycle-marker",
                    lifecycle="unexpected",
                    image_digest="b" * 64,
                )

        self.resource.line_rich_menu_id = "richmenu-1"
        self.resource.save(update_fields=("line_rich_menu_id",))
        for duplicate in (
            {"ownership_marker": self.resource.ownership_marker},
            {"line_rich_menu_id": self.resource.line_rich_menu_id},
        ):
            with self.subTest(duplicate=duplicate), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    values = {
                        "ownership_marker": "marker-" + uuid4().hex,
                        "line_rich_menu_id": None,
                        **duplicate,
                    }
                    ManagedRichMenu.objects.create(
                        channel_state=self.state,
                        origin_operation=self.apply_operation,
                        lifecycle="candidate",
                        image_digest="c" * 64,
                        **values,
                    )

    # テストケース: replacement operationとresource lifecycleのDB関係を保存する。
    # 期待値: oldではreplacementが必須、candidate/applied/releasedでは禁止される。
    def test_resource_replacement_relation_matches_lifecycle(self):
        replacement = self._create_operation(kind="apply")
        old = ManagedRichMenu.objects.create(
            channel_state=self.state,
            origin_operation=self.apply_operation,
            replacement_operation=replacement,
            ownership_marker="replacement-old-" + uuid4().hex,
            lifecycle="old",
            image_digest="d" * 64,
        )
        self.assertEqual(old.replacement_operation_id, replacement.operation_id)

        invalid_cases = (
            {"lifecycle": "candidate", "replacement_operation": replacement},
            {"lifecycle": "applied", "replacement_operation": replacement},
            {"lifecycle": "released", "replacement_operation": replacement, "released_at": timezone.now()},
        )
        for index, values in enumerate(invalid_cases):
            with self.subTest(values=values), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    ManagedRichMenu.objects.create(
                        channel_state=self.state,
                        origin_operation=self.apply_operation,
                        ownership_marker=f"invalid-replacement-{index}-{uuid4().hex}",
                        image_digest="e" * 64,
                        **values,
                    )

    # テストケース: 一つのreplacement operationを複数の旧資源へ関連付ける。
    # 期待値: one-to-one制約で二件目を拒否する。
    def test_replacement_operation_identifies_only_one_resource(self):
        replacement = self._create_operation(kind="apply")
        ManagedRichMenu.objects.create(
            channel_state=self.state,
            origin_operation=self.apply_operation,
            replacement_operation=replacement,
            ownership_marker="replacement-first-" + uuid4().hex,
            lifecycle="old",
            image_digest="f" * 64,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ManagedRichMenu.objects.create(
                channel_state=self.state,
                origin_operation=self.apply_operation,
                replacement_operation=replacement,
                ownership_marker="replacement-second-" + uuid4().hex,
                lifecycle="old",
                image_digest="1" * 64,
            )

    # テストケース: channel aggregateの一意性とappend-only transition sequenceを検証する。
    # 期待値: 同一channel stateと同一operation sequenceを二重登録できない。
    def test_channel_and_transition_uniqueness(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RichMenuChannelState.objects.create(
                    channel_public_id=self.state.channel_public_id
                )

        transition = RichMenuOperationTransition.objects.create(
            operation=self.apply_operation,
            sequence=1,
            from_status="accepted",
            to_status="processing",
            stage="creating",
            safe_reason="accepted",
            observed_at=timezone.now(),
        )
        transition.safe_reason = "changed"
        with self.assertRaises(ValidationError):
            transition.save()
        with self.assertRaises(ValidationError):
            transition.delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RichMenuOperationTransition.objects.create(
                    operation=self.apply_operation,
                    sequence=1,
                    from_status="processing",
                    to_status="failed",
                    stage="creating",
                    safe_reason="line_rejected",
                    observed_at=timezone.now(),
                )

    def _create_operation(
        self,
        *,
        kind,
        subject_operation=None,
        target_resource=None,
        status="accepted",
        stage=None,
    ):
        return RichMenuOperation.objects.create(
            operation_id=uuid4(),
            channel_state=self.state,
            owner_identity_public_id=uuid4(),
            provider_id="0012345678",
            kind=kind,
            subject_operation=subject_operation,
            target_resource=target_resource,
            request_fingerprint=uuid4().hex * 2,
            expected_channel_revision=timezone.now(),
            status=status,
            stage=stage,
            result_code="accepted",
            accepted_at=timezone.now(),
        )
