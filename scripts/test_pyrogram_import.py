from __future__ import annotations

import asyncio
import sys
from pathlib import Path

asyncio.set_event_loop(None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import bot_service  # noqa: F401,E402
import downloader  # noqa: F401,E402

print('PYROGRAM_IMPORT_OK')
