import os

from cryptography.fernet import Fernet


# テスト専用の鍵は base settings の読込前に、プロセスごとに生成する。
os.environ["LINE_CHANNEL_CREDENTIAL_KEYS"] = Fernet.generate_key().decode("ascii")
os.environ["DJANGO_DEBUG"] = "false"

from .settings import *  # noqa: E402,F403
