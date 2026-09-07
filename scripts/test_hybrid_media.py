from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from downloader import inspect_message_media


def main() -> None:
    chat = SimpleNamespace(id=-100123, title="channel")
    video = SimpleNamespace(file_size=123456)
    message = SimpleNamespace(
        id=42,
        empty=False,
        chat=chat,
        caption="وصف الفيديو",
        text=None,
        video=video,
        document=None,
        audio=None,
        voice=None,
        video_note=None,
        animation=None,
        photo=None,
    )
    details = inspect_message_media(message)
    assert details == {
        "type": "video",
        "size": 123456,
        "caption": "وصف الفيديو",
        "message_id": 42,
        "chat_id": -100123,
    }
    assert inspect_message_media(SimpleNamespace(empty=True)) is None
    assert inspect_message_media(SimpleNamespace(empty=False, video=None, document=None)) is None
    print("HYBRID_MEDIA_CHECK_OK")


if __name__ == "__main__":
    main()
