import json
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from ...cleanup_repair import CleanupRepairRejected, CleanupStateRepair


def build_repair_service():
    from lineaccounts.repositories import DjangoAccountRepository
    from linechannels import runtime
    from linechannels.admin_lifecycle_repositories import DjangoChannelDeactivationRepository
    from linechannels.admin_repositories import DjangoAdminChannelRepository
    from linechannels.crypto import FernetCredentialCipher

    return CleanupStateRepair(
        accounts=DjangoAccountRepository(),
        channels=DjangoAdminChannelRepository(
            FernetCredentialCipher(runtime.get_validated_keyring())
        ),
        deactivation=DjangoChannelDeactivationRepository(),
    )


class Command(BaseCommand):
    help = (
        "正常終了扱いで残った旧リッチメニューの後片付け状態を一件修復する。LINEへの変更は行わない。"
    )

    def add_arguments(self, parser):
        parser.add_argument("--channel-id", required=True, type=UUID)
        parser.add_argument("--expected-revision", required=True)
        parser.add_argument("--subject-operation-id", type=UUID)
        parser.add_argument("--target-resource-id", type=UUID)
        parser.add_argument(
            "--apply", action="store_true", help="dry-runで確認した操作と資源を指定して修復する"
        )

    def handle(self, *args, **options):
        try:
            revision = parse_datetime(options["expected_revision"])
            plan = build_repair_service().run(
                options["channel_id"],
                revision,
                apply=options["apply"],
                subject_id=options["subject_operation_id"],
                target_id=options["target_resource_id"],
            )
        except CleanupRepairRejected as error:
            raise CommandError(str(error)) from None
        except Exception:
            raise CommandError("cleanup_repair_unavailable") from None
        self.stdout.write(
            json.dumps(
                {
                    "status": plan.status,
                    "subjectOperationId": str(plan.subject_operation_id),
                    "targetResourceId": str(plan.target_resource_id),
                    "remainingOldResources": plan.remaining_old_resources,
                    "lineMutationCalls": 0,
                }
            )
        )
