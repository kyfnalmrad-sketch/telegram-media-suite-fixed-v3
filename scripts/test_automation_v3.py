from __future__ import annotations

import asyncio

from automation import DualAutomationProcessor
from bot_service import TelegramBotService, parse_chat_link
from downloader import parse_message_link


async def main() -> None:
    async def handler(job: int, worker_id: int) -> int:
        await asyncio.sleep(0)
        return job * 10 + worker_id

    results = await DualAutomationProcessor(workers=2).run([1, 2, 3, 4, 5], handler)
    assert len(results) == 5
    assert [item.value // 10 for item in results] == [1, 2, 3, 4, 5]
    assert {item.worker_id for item in results} <= {1, 2}

    async def failing_handler(job: int, worker_id: int) -> int:
        if job == 3:
            raise RuntimeError("test failure")
        return job + worker_id

    isolated = await DualAutomationProcessor(workers=2).run([1, 2, 3, 4], failing_handler)
    assert len(isolated) == 4
    assert isolated[2].error is not None
    assert isolated[0].error is None and isolated[3].error is None


asyncio.run(main())
service = TelegramBotService(lambda: None, lambda: "/tmp", lambda text: None)
assert service.pending_ttl_seconds == 24 * 60 * 60
assert parse_message_link("https://t.me/c/3328229190/168/378?single") == (-1003328229190, 378)
assert parse_chat_link("t.me/channel") == "channel"
assert parse_chat_link("t.me/+InviteCode") == "https://t.me/+InviteCode"
print("AUTOMATION_V3_OK")
