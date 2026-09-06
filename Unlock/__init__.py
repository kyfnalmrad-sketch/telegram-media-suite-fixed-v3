from __future__ import annotations

import base64
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Pyrogram 2.1.x imports its sync wrapper during module import and expects a
# current event loop. Python 3.14 no longer creates one automatically.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

API_ID = os.getenv("API_ID") or os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("API_HASH") or os.getenv("TELEGRAM_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
SESSION_STRING = os.getenv("SESSION_STRING") or os.getenv("TELEGRAM_SESSION_STRING") or os.getenv("TMD_SESSION_STRING")
DATA_DIR = Path(os.getenv("TMD_SUITE_DATA", "/tmp/restricted-content-saver"))
SESSION_DIR = DATA_DIR / "sessions"
DOWNLOAD_DIR = DATA_DIR / "downloads"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def restore_render_session() -> Path | None:
    encoded = os.getenv("TMD_SESSION_B64", "").strip()
    if not encoded:
        return None
    path = SESSION_DIR / "UserBot.session"
    if path.exists() and os.getenv("TMD_SESSION_B64_FORCE", "").lower() != "true":
        return path
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise RuntimeError("TMD_SESSION_B64 في Render غير صالح") from exc
    if not raw:
        raise RuntimeError("ملف جلسة Render فارغ")
    temporary = path.with_suffix(".session.tmp")
    temporary.write_bytes(raw)
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)
    return path


def require_clients() -> tuple[Client, Client]:
    """Build the Telegram clients only when the bot process is actually started.

    Keeping imports side-effect free lets unit tests exercise pure helper modules
    without requiring production secrets, while startup still fails early with a
    clear configuration error when credentials are missing.
    """
    if not all((API_ID, API_HASH, BOT_TOKEN)):
        raise RuntimeError("Render يحتاج API_ID وAPI_HASH وBOT_TOKEN")
    try:
        api_id = int(API_ID)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("API_ID يجب أن يكون رقمًا") from exc
    session_file = restore_render_session()
    if not SESSION_STRING and session_file is None:
        raise RuntimeError("Render يحتاج SESSION_STRING أو TMD_SESSION_B64 لجلسة الحساب الشخصي")
    bot = Client("Unlock", api_id=api_id, api_hash=API_HASH, bot_token=BOT_TOKEN, plugins={"root": "Unlock.modules"}, workdir=str(DATA_DIR))
    if SESSION_STRING:
        user = Client("UserBot", api_id=api_id, api_hash=API_HASH, session_string=SESSION_STRING, plugins={"root": "Unlock.modules.UserBot"})
    else:
        user = Client("UserBot", api_id=api_id, api_hash=API_HASH, plugins={"root": "Unlock.modules.UserBot"}, workdir=str(SESSION_DIR))
    return bot, user


rbot: Client | None = None
ubot: Client | None = None

_size_limit = os.getenv("ALLOWED_DOWNLOAD_SIZE", "").strip()
# The empty/default configuration accepts media up to 5 GiB; deployments can
# set a lower value in megabytes when their Render disk is smaller.
MAX_ALLOWED_DOWNLOAD_SIZE = float(_size_limit) if _size_limit else 5120.0
DEVELOPER = os.getenv("DEVELOPER", "")
REPO_LINK = os.getenv("REPO_LINK", "")
