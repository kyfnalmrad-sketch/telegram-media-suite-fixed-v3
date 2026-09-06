from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable

from .. import DATA_DIR

MAX_JOBS = int(os.getenv("MAX_QUEUE_JOBS", "20"))
STATE_FILE = DATA_DIR / "jobs.json"


class JobQueue:
    def __init__(self):
        self.jobs: dict[int, dict] = {}
        self.next_id = 1
        self.worker_task: asyncio.Task | None = None
        self.pause_event = asyncio.Event()
        self.pause_event.set()
        self.lock = threading.Lock()
        self.load()

    def load(self):
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.next_id = int(data.get("next_id", 1))
            self.jobs = {int(k): v for k, v in data.get("jobs", {}).items()}
            for job in self.jobs.values():
                if job.get("status") in {"downloading", "uploading", "processing"}:
                    job["status"] = "queued"
                    job["phase"] = "عاد بعد إعادة تشغيل الخدمة"
        except (OSError, ValueError, TypeError):
            self.jobs, self.next_id = {}, 1

    def save(self):
        payload = {"version": 1, "next_id": self.next_id, "jobs": self.jobs}
        temporary = STATE_FILE.with_suffix(".json.tmp")
        with self.lock:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, STATE_FILE)

    def add(self, chat_id: int, user_id: int, link: str, parsed: tuple[str | int, int]) -> dict:
        active = [j for j in self.jobs.values() if j.get("status") in {"queued", "processing", "downloading", "uploading", "paused"}]
        if len(active) >= MAX_JOBS:
            raise RuntimeError(f"قائمة الانتظار ممتلئة (الحد الأقصى {MAX_JOBS} عمليات)")
        chat_ref, message_id = parsed
        duplicate = next((j for j in active if j.get("chat_ref") == str(chat_ref) and j.get("message_id") == message_id), None)
        if duplicate:
            raise RuntimeError(f"هذا الرابط موجود بالفعل في العملية #{duplicate['id']}")
        job = {
            "id": self.next_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "link": link,
            "chat_ref": str(chat_ref),
            "message_id": message_id,
            "status": "queued",
            "phase": "في قائمة الانتظار",
            "progress": 0,
            "current": 0,
            "total": 0,
            "error": "",
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        self.jobs[self.next_id] = job
        self.next_id += 1
        self.save()
        return job

    def update(self, job_id: int, **values):
        job = self.jobs.get(job_id)
        if not job:
            return
        job.update(values)
        job["updated_at"] = int(time.time())
        self.save()

    def get(self, job_id: int) -> dict | None:
        return self.jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[dict]:
        return sorted(self.jobs.values(), key=lambda j: j["id"], reverse=True)[:limit]

    async def start(self, processor: Callable[[dict], Awaitable[None]]):
        if self.worker_task and not self.worker_task.done():
            return
        self.worker_task = asyncio.create_task(self._worker(processor))

    async def _worker(self, processor):
        while True:
            await self.pause_event.wait()
            job = next((j for j in sorted(self.jobs.values(), key=lambda x: x["id"]) if j.get("status") == "queued"), None)
            if not job:
                await asyncio.sleep(1)
                continue
            self.update(job["id"], status="processing", phase="قيد المعالجة", error="")
            try:
                await processor(job)
                if job.get("status") not in {"cancelled", "failed"}:
                    self.update(job["id"], status="completed", phase="اكتمل — جاهز للمشاهدة والتنزيل", progress=100)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.update(job["id"], status="failed", phase="فشل — يمكن إعادة المحاولة", error=type(exc).__name__)

    def pause(self):
        self.pause_event.clear()
        self.save()

    def resume(self):
        self.pause_event.set()
        self.save()

    def cancel(self, job_id: int) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.get("status") in {"completed", "cancelled"}:
            return False
        job.update(status="cancelled", phase="تم الإلغاء")
        self.save()
        return True

    def retry(self, job_id: int) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.get("status") not in {"failed", "cancelled"}:
            return False
        job.update(status="queued", phase="أعيد إلى قائمة الانتظار", error="", progress=0, current=0, total=0)
        self.save()
        return True


queue = JobQueue()


def queue_snapshot() -> dict:
    return {"paused": not queue.pause_event.is_set(), "jobs": queue.recent()}
