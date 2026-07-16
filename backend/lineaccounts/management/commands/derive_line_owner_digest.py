import os
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from lineaccounts.runtime import derive_owner_digest, get_line_account_runtime


class Command(BaseCommand):
    help = "LINE owner subject digestを秘密入力から生成します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--use-line-user-id",
            action="store_true",
            help="Backend専用LINE_USER_IDを非表示の入力源として使用します。",
        )

    def handle(self, *args, **options):
        if options["use_line_user_id"]:
            subject = os.environ.get("LINE_USER_ID", "")
        else:
            subject = getpass("LINE owner subject: ")

        if not subject:
            raise CommandError("OWNER_DIGEST_INPUT_INVALID")

        runtime = get_line_account_runtime()
        self.stdout.write(derive_owner_digest(runtime.provider_id, subject))

