from __future__ import annotations

import logging

from pyrogram import idle

from . import rbot, ubot
from .modules.job_queue import queue
from .modules.send_restricted import process_job


async def main() -> None:
    logging.info("Starting bot and user session")
    await rbot.start()
    try:
        await ubot.start()
        await queue.start(lambda job: process_job(rbot, job))
        logging.info("Bot and user session are ready")
        await idle()
    finally:
        if ubot.is_connected:
            await ubot.stop()
        if rbot.is_connected:
            await rbot.stop()
        logging.info("Services stopped")


if __name__ == "__main__":
    rbot.run(main())
