from __future__ import annotations

import logging

from pyrogram import idle

from . import require_clients
from .modules.job_queue import queue
from .modules.send_restricted import process_job
from .modules.service_log import record_service_event


async def main() -> None:
    logging.info("Starting bot and user session")
    record_service_event("bot_starting", "بدء تشغيل عميل البوت والحساب")
    rbot, ubot = require_clients()
    await rbot.start()
    try:
        await ubot.start()
        await queue.start(lambda job: process_job(rbot, job))
        logging.info("Bot and user session are ready")
        record_service_event("bot_ready", "البوت والحساب جاهزان")
        await idle()
    finally:
        if ubot.is_connected:
            await ubot.stop()
        if rbot.is_connected:
            await rbot.stop()
        record_service_event("bot_stopped", "تم إيقاف خدمات Telegram")
        logging.info("Services stopped")


if __name__ == "__main__":
    rbot.run(main())
