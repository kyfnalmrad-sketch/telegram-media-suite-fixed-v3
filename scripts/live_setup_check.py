from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from pyrogram import Client


def read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result

api = read_env(Path("/home/ubuntu/upload/telegram-api"))
token_env = read_env(Path("/home/ubuntu/upload/telegram-bot-token"))
with tempfile.TemporaryDirectory(prefix="tmd-live-check-") as temp:
    workdir = Path(temp)
    shutil.copy2(
        "/home/ubuntu/telegram_media_downloader/telegram_media_downloader-master/telegram_media_downloader-master/sessions/media_downloader.session",
        workdir / "tmd_user.session",
    )
    os.chmod(workdir / "tmd_user.session", 0o600)
    user = Client("tmd_user", api_id=int(api["TELEGRAM_API_ID"]), api_hash=api["TELEGRAM_API_HASH"], workdir=str(workdir), no_updates=True)
    authorized = user.connect()
    print(f"USER_SESSION_AUTHORIZED={authorized}")
    owner_id = 0
    if authorized:
        owner_id = int(user.get_me().id)
        print(f"OWNER_USER_ID={owner_id}")
        user.disconnect()
    bot = Client("tmd_bot", api_id=int(api["TELEGRAM_API_ID"]), api_hash=api["TELEGRAM_API_HASH"], bot_token=token_env["TELEGRAM_BOT_TOKEN"], workdir=str(workdir), no_updates=True)
    try:
        bot.start()
        print("BOT_START_OK=True")
        print(f"BOT_ID_PRESENT={bool(bot.get_me() and bot.get_me().id)}")
        bot.stop()
    except Exception as exc:
        print(f"BOT_START_OK=False")
        print(f"BOT_ERROR_TYPE={type(exc).__name__}")
        print(f"BOT_ERROR_TEXT={str(exc)[:160]}")
