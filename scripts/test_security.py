from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

TOKEN = 'security-test-token'
os.environ['RENDER'] = 'true'
os.environ['TMD_DASHBOARD_TOKEN'] = TOKEN
os.environ['TMD_SUITE_DATA'] = tempfile.mkdtemp(prefix='tmd-security-')
os.environ['TMD_OPEN_BROWSER'] = 'false'
os.environ['API_ID'] = ''
os.environ['API_HASH'] = ''
os.environ['BOT_TOKEN'] = ''
os.environ['ALLOWED_USER_IDS'] = ''

from bot_service import TelegramBotService
from web_app import app


class FakeMessage:
    def __init__(self, user_id: int, text: str) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.text = text
        self.caption = ''
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append(text)


async def check_private_bot() -> None:
    service = TelegramBotService(lambda: None, lambda: '/tmp', lambda _: None, self_enrollment_enabled=False)
    message = FakeMessage(123, '/start')
    handler = service._message_handler()
    await handler.callback(None, message)
    assert message.replies == ['هذا البوت خاص حاليًا. اطلب من المالك إضافة User ID الخاص بك.']


client = app.test_client()
assert client.get('/health').status_code == 200
assert client.get('/').status_code == 401
assert client.get('/api/state').status_code == 401
assert client.get('/', headers={'X-Dashboard-Token': TOKEN}).status_code == 200
assert client.get('/api/state', headers={'Authorization': f'Bearer {TOKEN}'}).status_code == 200
asyncio.run(check_private_bot())
print('SECURITY_OK')
