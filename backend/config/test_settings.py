import os
import secrets
from uuid import uuid4

from cryptography.fernet import Fernet


# テスト専用の鍵は base settings の読込前に、プロセスごとに生成する。
os.environ["LINE_CHANNEL_CREDENTIAL_KEYS"] = Fernet.generate_key().decode("ascii")
os.environ["DJANGO_SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["DJANGO_DEBUG"] = "false"
os.environ["NGROK_DOMAIN"] = "test.example.ngrok.app"
os.environ["LINE_LOGIN_CHANNEL_ID"] = "1234567890"
os.environ["LINE_LOGIN_CHANNEL_SECRET"] = secrets.token_urlsafe(48)
os.environ["LINE_LOGIN_PROVIDER_ID"] = "0012345678"
os.environ["LINE_LIFF_LINKED_CHANNEL_PUBLIC_ID"] = str(uuid4())
os.environ.pop("LINE_OWNER_SUBJECT_DIGEST", None)

from .settings import *  # noqa: E402,F403
