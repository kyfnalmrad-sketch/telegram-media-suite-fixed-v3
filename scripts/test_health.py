from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        os.environ["TMD_SUITE_DATA"] = temporary
        os.environ["TMD_BUILD_VERSION"] = "test-hybrid"
        os.environ["TMD_DASHBOARD_TOKEN"] = ""
        from web_app import app

        response = app.test_client().get("/health")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["service"] == "telegram-media-suite"
        assert payload["version"] == "test-hybrid"
        assert payload["session"] in {"phone", "not_started", "ready", "error"}
        assert payload["session_file"] in {"ready", "missing"}
        assert payload["storage"] in {"writable", "unavailable"}
    print("HEALTH_CHECK_OK")


if __name__ == "__main__":
    main()
