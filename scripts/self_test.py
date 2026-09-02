from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from config_store import load_settings, save_settings  # noqa: E402
from downloader import parse_message_link, safe_name  # noqa: E402
from bot_service import TelegramBotService  # noqa: E402

assert parse_message_link("https://t.me/example/42") == ("example", 42)
assert parse_message_link("https://t.me/c/123456789/42") == (-100123456789, 42)
assert safe_name('bad:/name*') == "bad__name_"
bot = TelegramBotService(lambda: None, lambda: "/tmp", lambda _: None)
assert bot._message_handler() is not None

with tempfile.TemporaryDirectory() as temp:
    os.environ["TMD_SUITE_DATA"] = temp
    # The module constants are intentionally not reloaded; test pure helpers here.
    assert load_settings()["storage_path"]

print("SUITE_SELF_TEST_OK")
