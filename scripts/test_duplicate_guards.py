from __future__ import annotations

import sys
from pathlib import Path
import asyncio
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from bot_service import TelegramBotService


service = TelegramBotService(lambda: None, lambda: '/tmp', lambda _: None)
message = SimpleNamespace(id=77, chat=SimpleNamespace(id=123), from_user=SimpleNamespace(id=123))
assert service._is_duplicate_update(message) is False
assert service._is_duplicate_update(message) is True
callback = SimpleNamespace(id='callback-77', from_user=SimpleNamespace(id=123), data='status')
assert service._is_duplicate_callback(callback) is False
assert service._is_duplicate_callback(callback) is True
assert service._start_reply_allowed(123) is True
assert service._start_reply_allowed(123) is False
assert service._unique_links([
    'https://t.me/channel/10?single',
    'https://t.me/channel/10',
    'https://t.me/channel/11',
]) == ['https://t.me/channel/10?single', 'https://t.me/channel/11']


class FakeStartMessage:
    def __init__(self):
        self.id = 88
        self.chat = SimpleNamespace(id=123)
        self.from_user = SimpleNamespace(id=123)
        self.text = '/start'
        self.caption = None
        self.replies = 0

    async def reply_photo(self, *args, **kwargs):
        self.replies += 1

    async def reply_text(self, *args, **kwargs):
        self.replies += 1


async def check_handler():
    handler = service._message_handler().callback
    service.allowed_user_ids = {123}
    service._last_start_reply.clear()
    message = FakeStartMessage()
    await handler(None, message)
    await handler(None, message)
    assert message.replies == 1


asyncio.run(check_handler())
assert service._reply_allowed(456, 'private') is True
assert service._reply_allowed(456, 'private') is False
print('DUPLICATE_GUARDS_OK')
