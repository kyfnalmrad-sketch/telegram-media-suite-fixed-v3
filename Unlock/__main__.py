from __future__ import annotations

import logging

from pyrogram import idle

from . import rbot, ubot


async def main() -> None:
    logging.info("Starting bot and user session")
    await rbot.start()
    try:
        await ubot.start()
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
