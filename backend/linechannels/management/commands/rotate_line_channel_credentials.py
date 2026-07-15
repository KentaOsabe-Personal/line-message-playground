import json

from django.core.management.base import BaseCommand, CommandError

from ...rotation import RotationSummary


def build_rotation_service():
    from ...container import build_rotation_service as build

    return build()


class Command(BaseCommand):
    help = "Rotate stored LINE channel credentials using the validated process keyring."

    def handle(self, *args, **options):
        try:
            service = build_rotation_service()
            summary = service.rotate_all()
        except (KeyboardInterrupt, Exception):
            raise CommandError("credential rotation failed") from None

        if not isinstance(summary, RotationSummary):
            raise CommandError("credential rotation failed")

        output = {
            "status": summary.status,
            "verified_count": summary.verified_count,
            "rotated_count": summary.rotated_count,
            "failed_count": summary.failed_count,
            "old_keys_removable": summary.old_keys_removable,
        }
        if summary.failures:
            output["failures"] = [
                {
                    "channel_public_id": str(failure.channel_public_id),
                    "code": failure.code,
                }
                for failure in summary.failures
            ]

        rendered = json.dumps(output, ensure_ascii=False, sort_keys=True)
        if summary.status == "complete":
            self.stdout.write(rendered)
            return

        if summary.status in ("incomplete", "busy", "configuration_required"):
            self.stderr.write(rendered)
            raise CommandError("credential rotation incomplete")
        raise CommandError("credential rotation failed")
