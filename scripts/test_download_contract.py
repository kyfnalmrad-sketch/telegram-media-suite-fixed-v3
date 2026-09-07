from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from downloader import TelegramSession


class FakeClient:
    async def download_media(self, message, file_name, progress):
        target = Path(file_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video-data")
        progress(5, 10)
        progress(10, 10)
        return str(target)


async def run_check() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        session = TelegramSession("123", "h" * 32, temporary, lambda _: None)
        session.client = FakeClient()
        message = SimpleNamespace(
            id=77,
            empty=False,
            media="video",
            date=datetime(2026, 9, 7, tzinfo=timezone.utc),
            caption="وصف",
            text=None,
            chat=SimpleNamespace(title="قناة اختبار"),
            video=SimpleNamespace(file_name="clip.mp4", file_size=10),
            document=None,
            audio=None,
            voice=None,
            video_note=None,
            photo=None,
            animation=None,
        )
        progress_values = []
        result = await session._download_message(message, temporary, lambda current, total: progress_values.append((current, total)))
        output = Path(result["path"])
        assert output.exists()
        assert output.read_bytes() == b"video-data"
        assert result["channel"] == "قناة اختبار"
        assert result["message_id"] == 77
        assert progress_values[-1] == (10, 10)


if __name__ == "__main__":
    asyncio.run(run_check())
    print("DOWNLOAD_CONTRACT_OK")
