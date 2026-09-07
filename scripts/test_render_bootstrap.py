from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import render_start


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        render_start.DATA_DIR = root
        render_start.SESSION_DIR = root / "sessions"
        render_start.STORAGE_DIR = root / "storage"
        render_start.SETTINGS_FILE = root / "settings.json"
        os.environ["TMD_SESSION_B64"] = base64.b64encode(b"session-bytes").decode()
        os.environ["TMD_SESSION_B64_FORCE"] = "false"
        os.environ["API_ID"] = "12345"
        os.environ["ALLOWED_USER_IDS"] = "42"
        render_start.bootstrap()
        assert (root / "sessions" / "tmd_user.session").read_bytes() == b"session-bytes"
        settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
        assert settings["session_path"] == str(root / "sessions")
        assert settings["storage_path"] == str(root / "storage")
        assert settings["api_id"] == "12345"
        assert settings["allowed_user_ids"] == "42"
    print("RENDER_BOOTSTRAP_OK")


if __name__ == "__main__":
    main()
