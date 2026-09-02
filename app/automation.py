from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Sequence, TypeVar


JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")


@dataclass(slots=True)
class WorkerResult(Generic[JobT, ResultT]):
    """نتيجة مهمة واحدة مع رقم المعالج والخطأ إن حدث."""

    index: int
    job: JobT
    worker_id: int
    value: ResultT | None = None
    error: Exception | None = None


class DualAutomationProcessor(Generic[JobT, ResultT]):
    """طابور ثنائي لمعالجة مهام Telegram دون إغراق الجلسة أو فقدان نتيجة."""

    def __init__(self, workers: int = 2) -> None:
        self.workers = max(1, min(2, int(workers)))

    async def run(
        self,
        jobs: Sequence[JobT],
        handler: Callable[[JobT, int], Awaitable[ResultT]],
    ) -> list[WorkerResult[JobT, ResultT]]:
        queue: asyncio.Queue[tuple[int, JobT] | None] = asyncio.Queue()
        for index, job in enumerate(jobs):
            queue.put_nowait((index, job))
        for _ in range(self.workers):
            queue.put_nowait(None)

        results: list[WorkerResult[JobT, ResultT] | None] = [None] * len(jobs)

        async def consume(worker_id: int) -> None:
            while True:
                entry = await queue.get()
                try:
                    if entry is None:
                        return
                    index, job = entry
                    try:
                        value = await handler(job, worker_id)
                        results[index] = WorkerResult(index, job, worker_id, value=value)
                    except Exception as exc:  # keep the other worker running
                        results[index] = WorkerResult(index, job, worker_id, error=exc)
                finally:
                    queue.task_done()

        tasks = [asyncio.create_task(consume(worker_id)) for worker_id in range(1, self.workers + 1)]
        await queue.join()
        await asyncio.gather(*tasks)
        return [item for item in results if item is not None]
