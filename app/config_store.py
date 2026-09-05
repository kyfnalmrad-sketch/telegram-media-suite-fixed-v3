from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("TMD_SUITE_DATA", Path.home() / ".telegram_media_downloader_suite"))
SETTINGS_FILE = APP_DIR / "settings.json"
ENV_FILE = APP_DIR / ".env"
DEFAULTS: dict[str, Any] = {
    "api_id": "",
    "api_hash": "",
    "bot_token": "",
    "phone": "",
    "storage_path": str(APP_DIR / "storage"),
    "session_path": str(APP_DIR / "sessions"),
    "allowed_user_ids": "",
    "auto_start": False,
    "render_service_id": "srv-dac9q2mk1f9s73brjvr0",
}
SECRET_KEYS = {"api_hash", "bot_token", "phone"}
ENV_NAMES = {
    "api_id": "API_ID",
    "api_hash": "API_HASH",
    "bot_token": "BOT_TOKEN",
    "phone": "PHONE",
    "storage_path": "STORAGE_PATH",
    "session_path": "SESSION_PATH",
    "allowed_user_ids": "ALLOWED_USER_IDS",
    "auto_start": "AUTO_START",
    "render_service_id": "RENDER_SERVICE_ID",
}


def _load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    try:
        for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('\"').strip("'")
    except OSError:
        pass
    return values


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "نعم"}


def _apply_env(data: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    for setting_key, env_key in ENV_NAMES.items():
        value = os.environ.get(env_key, env.get(env_key, ""))
        if not value:
            continue
        data[setting_key] = _is_true(value) if setting_key == "auto_start" else value
    return data


def save_secrets(values: dict[str, str]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    existing = _load_env()
    for key, value in values.items():
        if value.strip():
            existing[key] = value.strip()
    ENV_FILE.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(existing.items())),
        encoding="utf-8",
    )
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass


def load_settings() -> dict[str, Any]:
    data = dict(DEFAULTS)
    env = _load_env()
    if SETTINGS_FILE.exists():
        try:
            loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    return _apply_env(data, env)


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    data = dict(DEFAULTS)
    data.update({key: values.get(key, DEFAULTS[key]) for key in DEFAULTS if key not in SECRET_KEYS})
    save_secrets({ENV_NAMES[key]: str(values.get(key, "")) for key in SECRET_KEYS})
    APP_DIR.mkdir(parents=True, exist_ok=True)
    path_tmp = SETTINGS_FILE.with_suffix(".tmp")
    path_tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(path_tmp, SETTINGS_FILE)
    return load_settings()


def secret_state(value: str) -> str:
    return "مضبوط" if value else "غير مضبوط"
