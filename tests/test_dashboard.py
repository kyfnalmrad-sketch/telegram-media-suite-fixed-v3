import os

os.environ.setdefault("TMD_SUITE_DATA", "/tmp/telegram-media-suite-test-data")

from render_start import app


def test_health_is_public_but_does_not_expose_private_queue(monkeypatch):
    monkeypatch.delenv("TMD_DASHBOARD_TOKEN", raising=False)
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "service": "running"}


def test_dashboard_token_protects_api_and_can_login_once(monkeypatch):
    monkeypatch.setenv("TMD_DASHBOARD_TOKEN", "secret-token")
    client = app.test_client()

    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401

    page = client.get("/?token=secret-token")
    assert page.status_code == 200
    assert "tmd_dashboard_token" in page.headers.get("Set-Cookie", "")
    assert client.get("/api/status").status_code == 200


def test_dashboard_token_accepts_bearer_header(monkeypatch):
    monkeypatch.setenv("TMD_DASHBOARD_TOKEN", "secret-token")
    client = app.test_client()

    response = client.get("/api/status", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
