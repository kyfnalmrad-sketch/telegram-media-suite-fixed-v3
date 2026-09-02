from __future__ import annotations

import base64
import json
import os
from pathlib import Path


DATA_DIR = Path(os.environ.get("TMD_SUITE_DATA", "/opt/render/project/src/.tmd-data"))
SESSION_DIR = DATA_DIR / "sessions"
STORAGE_DIR = DATA_DIR / "storage"
SETTINGS_FILE = DATA_DIR / "settings.json"


def bootstrap() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    encoded_session = os.environ.get("TMD_SESSION_B64", "").strip()
    # Never overwrite a live/newer session with a stale environment snapshot.
    # Set TMD_SESSION_B64_FORCE=true only when intentionally restoring a backup.
    force_restore = os.environ.get("TMD_SESSION_B64_FORCE", "").strip().lower() == "true"
    session_file = SESSION_DIR / "tmd_user.session"
    if encoded_session and (force_restore or not session_file.exists()):
        session_bytes = base64.b64decode(encoded_session, validate=True)
        temporary_session = session_file.with_suffix(".session.tmp")
        temporary_session.write_bytes(session_bytes)
        temporary_session.chmod(0o600)
        journal_file = session_file.with_name(session_file.name + "-journal")
        try:
            journal_file.unlink()
        except FileNotFoundError:
            pass
        os.replace(temporary_session, session_file)
        session_file.chmod(0o600)

    settings = {
        "api_id": os.environ.get("API_ID", ""),
        "storage_path": str(STORAGE_DIR),
        "session_path": str(SESSION_DIR),
        "allowed_user_ids": os.environ.get("ALLOWED_USER_IDS", ""),
        "auto_start": True,
    }
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        SETTINGS_FILE.chmod(0o600)


def main() -> None:
    bootstrap()
    port = os.environ.get("PORT", "10000")
    os.environ.setdefault("TMD_SUITE_DATA", str(DATA_DIR))
    os.environ.setdefault("TMD_HOST", "0.0.0.0")
    os.environ.setdefault("TMD_OPEN_BROWSER", "false")
    os.execvp(
        "gunicorn",
        [
            "gunicorn",
            "--chdir",
            "app",
            "--workers",
            "1",
            "--threads",
            "8",
            "--timeout",
            "0",
            "--bind",
            f"0.0.0.0:{port}",
            "web_app:app",
        ],
    )


if __name__ == "__main__":
    main()
