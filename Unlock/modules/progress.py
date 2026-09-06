from __future__ import annotations

import asyncio
import time
from pathlib import Path


def format_bytes(value: int | float | None) -> str:
    amount = max(0.0, float(value or 0))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "0 B"


def progress_bar(current: int, total: int, width: int = 12) -> str:
    percent = int(current * 100 / total) if total else 0
    filled = min(width, max(0, percent * width // 100))
    return f"[{'■' * filled}{'□' * (width - filled)}] {percent}%"


def transfer_text(label: str, current: int, total: int, speed: float = 0) -> str:
    size = f"{format_bytes(current)} / {format_bytes(total)}" if total else format_bytes(current)
    rate = f" • {format_bytes(speed)}/ث" if speed > 0 else ""
    return f"{label}\n{progress_bar(current, total)}\n{size}{rate}"


class ProgressReporter:
    """Persist transfer progress and safely throttle Telegram message edits."""

    def __init__(self, message, job_id: int | None = None, interval: float = 2.0):
        self.message = message
        self.job_id = job_id
        self.interval = interval
        self._last_edit = 0.0
        self._last_publish = 0.0
        self._started_at = time.monotonic()
        self._edit_lock = asyncio.Lock()

    def update_sync(
        self,
        label: str,
        current: int,
        total: int,
        *,
        phase: str | None = None,
        status: str | None = None,
    ) -> None:
        current = max(0, int(current or 0))
        total = max(0, int(total or 0))
        now = time.monotonic()
        if total and current < total and now - self._last_publish < self.interval:
            return
        self._last_publish = now
        elapsed = max(0.001, now - self._started_at)
        speed = current / elapsed
        percent = int(current * 100 / total) if total else 0
        if self.job_id is not None:
            from .job_queue import queue
            values = {"progress": percent, "current": current, "total": total, "speed": int(speed)}
            if phase:
                values["phase"] = phase
            if status:
                values["status"] = status
            queue.update(self.job_id, **values)
        if self.message is not None:
            text = transfer_text(label, current, total, speed)
            asyncio.create_task(self._edit(text))

    async def _edit(self, text: str, force: bool = False) -> None:
        if self.message is None:
            return
        async with self._edit_lock:
            now = time.monotonic()
            if not force and now - self._last_edit < self.interval:
                return
            try:
                await self.message.edit_text(text)
                self._last_edit = time.monotonic()
            except Exception:
                # A deleted message or a transient Telegram edit failure must
                # not turn a successful media transfer into a failed job.
                return

    async def finish(
        self,
        text: str,
        *,
        phase: str | None = None,
        status: str | None = None,
        progress: int | None = None,
    ) -> None:
        if self.job_id is not None:
            from .job_queue import queue
            values = {}
            if phase:
                values["phase"] = phase
            if status:
                values["status"] = status
            if progress is not None:
                values["progress"] = progress
            if values:
                queue.update(self.job_id, **values)
        await self._edit(text, force=True)


__all__ = ["ProgressReporter", "format_bytes", "progress_bar", "transfer_text"]
