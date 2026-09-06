from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from .. import DATA_DIR

SERVICE_LOG = DATA_DIR / "service_events.jsonl"
SERVICE_LOCK = DATA_DIR / "service_events.lock"


def record_service_event(event: str, details: str = "", *, job_id: int | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"at": int(time.time()), "source": "bot", "event": event, "details": details}
    if job_id is not None:
        entry["job_id"] = job_id
    SERVICE_LOCK.touch(exist_ok=True)
    with SERVICE_LOCK.open("r+") as lock_file:
        if fcntl:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            try:
                lines = SERVICE_LOG.read_text(encoding="utf-8").splitlines()[-199:]
            except OSError:
                lines = []
            lines.append(json.dumps(entry, ensure_ascii=False))
            temporary = SERVICE_LOG.with_suffix(".tmp")
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(temporary, SERVICE_LOG)
        finally:
            if fcntl:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


__all__ = ["record_service_event"]
