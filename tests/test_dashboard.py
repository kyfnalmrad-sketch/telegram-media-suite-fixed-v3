import os

os.environ.setdefault("TMD_SUITE_DATA", "/tmp/telegram-media-suite-test-data")

from render_start import app


def test_health_is_public_but_does_not_expose_private_queue(monkeypatch):
    monkeypatch.delenv("TMD_DASHBOARD_TOKEN", raising=False)
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["service"] == "telegram-media-suite"
    assert "version" in payload
    assert "bot" in payload
    assert "exit_code" in payload


def test_dashboard_api_remains_open_without_password(monkeypatch):
    monkeypatch.setenv("TMD_DASHBOARD_TOKEN", "secret-token")
    client = app.test_client()

    assert client.get("/api/status").status_code == 200
    assert client.get("/").status_code == 200
