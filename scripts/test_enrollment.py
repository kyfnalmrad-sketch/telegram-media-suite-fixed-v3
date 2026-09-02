from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from bot_service import TelegramBotService


class FakeMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = SimpleNamespace(id=user_id, first_name="Test")
        self.text = ""
        self.caption = ""
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append(text)
        return SimpleNamespace(edit_text=self.reply_text)

    async def reply_photo(self, path: str, **kwargs):
        assert Path(path).name == "wolf_background.png"
        self.replies.append("PHOTO")
        return SimpleNamespace(edit_text=self.reply_text)


async def main() -> None:
    saved: list[set[int]] = []
    service = TelegramBotService(lambda: None, lambda: "/tmp", lambda text: None, lambda ids: saved.append(ids))
    service.brand_image = Path(__file__).resolve().parents[1] / "assets" / "wolf_background.png"
    message = FakeMessage(987654321)
    await service._begin_enrollment(message)
    assert service.user_flows[987654321]["mode"] == "enroll_confirm"
    await service._continue_enrollment(message, "نعم")
    assert 987654321 in service.allowed_user_ids
    assert saved == [{987654321}]
    assert "تمت الموافقة وحفظ User ID. أصبح الحساب مصرحًا له." in message.replies

    rejected = FakeMessage(123456789)
    await service._begin_enrollment(rejected)
    await service._continue_enrollment(rejected, "لا")
    assert 123456789 not in service.allowed_user_ids
    print("ENROLLMENT_FLOW_OK")


asyncio.run(main())
