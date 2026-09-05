from __future__ import annotations
import json
import os
import sys
from unittest.mock import patch

os.environ['TMD_SUITE_DATA'] = '/tmp/tmd-render-import-test'
os.environ['RENDER'] = 'true'
os.environ['TMD_DASHBOARD_TOKEN'] = 'test-token'
sys.path.insert(0, 'app')
import web_app  # noqa: E402

class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode()

env_payload = [
    {'key': 'API_ID', 'value': '12345'},
    {'key': 'API_HASH', 'value': 'hash-value'},
    {'key': 'BOT_TOKEN', 'value': 'bot-value'},
    {'key': 'PHONE', 'value': '+10000000000'},
    {'key': 'ALLOWED_USER_IDS', 'value': '42'},
]
with patch.object(web_app, 'urlopen', return_value=FakeResponse(env_payload)):
    result = web_app.import_render_environment('render-key', 'srv-test')
    assert result['count'] == 5
    assert result['editable']['API_HASH'] == 'hash-value'

calls = []
def fake_urlopen(request, timeout=20):
    calls.append((request.method, request.full_url, json.loads(request.data.decode())))
    return FakeResponse({})

with patch.object(web_app, 'urlopen', side_effect=fake_urlopen):
    updated = web_app.sync_render_environment(
        {'api_id': '12345', 'api_hash': 'hash-value', 'bot_token': 'bot-value', 'phone': '+10000000000'},
        'render-key', 'srv-test'
    )
    assert updated == ['API_ID', 'API_HASH', 'BOT_TOKEN', 'PHONE']
    assert len(calls) == 4
    assert calls[0][0] == 'PUT'

print('RENDER_IMPORT_SYNC_OK')
