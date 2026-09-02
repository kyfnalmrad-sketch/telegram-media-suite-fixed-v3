from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from bot_service import TelegramBotService


def item(message_id: int, day: int, name: str):
    return SimpleNamespace(
        id=message_id,
        empty=False,
        media='video',
        date=datetime(2026, 1, day, tzinfo=timezone.utc),
        from_user=SimpleNamespace(id=100 + message_id, first_name='Test', last_name='User', username=f'user{message_id}'),
        sender_chat=None,
        caption=f'وصف {name}',
        text='',
        video=SimpleNamespace(file_name=name, file_size=10),
    )


service = TelegramBotService(lambda: None, lambda: '/tmp', lambda _: None)
messages = [item(20, 3, 'new.mp4'), item(10, 1, 'old.mp4'), item(20, 3, 'duplicate.mp4')]
ordered = service._ordered_unique_media(messages)
assert [m.id for m in ordered] == [10, 20]
assert service._media_file_name(ordered[0]) == 'old.mp4'
assert 'اسم الملف الأصلي: old.mp4' in service._channel_item_report(ordered[0], 1)


class FakeMessage:
    def __init__(self):
        self.from_user = SimpleNamespace(id=123)
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


async def check_picker():
    message = FakeMessage()
    flow = {'mode': 'channel_full_year', 'chat_ref': -100123}
    await service._show_date_picker(message, flow, 'year')
    text, kwargs = message.replies[-1]
    assert 'سنة' in text
    callbacks = [button.callback_data for row in kwargs['reply_markup'].inline_keyboard for button in row]
    assert any(str(callback).startswith('scan_date_year:') for callback in callbacks)

    flow['year'] = 2026
    await service._show_date_picker(message, flow, 'month')
    text, kwargs = message.replies[-1]
    labels = [button.text for row in kwargs['reply_markup'].inline_keyboard for button in row]
    assert 'يناير' in labels and 'فبراير' in labels
    callbacks = [button.callback_data for row in kwargs['reply_markup'].inline_keyboard for button in row]
    assert 'scan_date_month:1' in callbacks

    flow['month'] = 2
    await service._show_date_picker(message, flow, 'day')
    _, kwargs = message.replies[-1]
    callbacks = [button.callback_data for row in kwargs['reply_markup'].inline_keyboard for button in row]
    assert 'scan_date_day:28' in callbacks
    assert 'scan_date_day:29' not in callbacks


asyncio.run(check_picker())
print('CHANNEL_SCAN_OK')
