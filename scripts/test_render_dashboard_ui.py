from __future__ import annotations
import os
import sys

os.environ['RENDER'] = 'true'
os.environ['API_ID'] = '12345'
os.environ['API_HASH'] = 'hidden'
os.environ['TMD_DASHBOARD_TOKEN'] = 'test-token'
sys.path.insert(0, 'app')
import web_app  # noqa: E402

with web_app.app.test_client() as client:
    health = client.get('/health')
    page = client.get('/', headers={'X-Dashboard-Token': 'test-token'})
    state_response = client.get('/api/state', headers={'X-Dashboard-Token': 'test-token'})
    state = state_response.get_json()
    assert health.status_code == 200, health.status_code
    assert page.status_code == 200, page.status_code
    assert state_response.status_code == 200, state_response.status_code
    assert state['runtime']['render'] is True, state
    assert state['settings']['api_id_set'] is True, state
    assert 'hidden' not in state_response.get_data(as_text=True), state_response.get_data(as_text=True)
print('WEB_RENDER_CONFIG_UI_OK')
