from __future__ import annotations

import asyncio
import logging
import time

from pyrogram import idle

from . import require_clients
from .modules.job_queue import queue
from .modules.send_restricted import process_job
from .modules.service_log import record_service_event


async def main() -> None:
    logging.info("Starting bot and user session")
    record_service_event("bot_starting", "بدء تشغيل عميل البوت والحساب")
    rbot = ubot = None
    try:
        rbot, ubot = require_clients()
        # saver.py imports the package-level client references. Keep those
        # references synchronized with the clients created for this process;
        # otherwise link inspection reaches ``None.get_messages`` and reports
        # the misleading AttributeError seen in the bot chat.
        import Unlock
        from .modules.UserBot import saver as saver_module
        Unlock.rbot = rbot
        Unlock.ubot = ubot
        saver_module.ubot = ubot
        await rbot.start()
        await ubot.start()
        await queue.start(lambda job: process_job(rbot, job))
        logging.info("Bot and user session are ready")
        record_service_event("bot_ready", "البوت والحساب جاهزان")
        await idle()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        record_service_event("bot_start_failed", f"{type(exc).__name__}: {exc}")
        logging.exception("Bot process failed")
        raise
    finally:
        # Startup can fail before one or both clients are assigned. Cleanup is
        # deliberately defensive so a Telegram error never masks the real one.
        for client, label in ((ubot, "user"), (rbot, "bot")):
            try:
                if client is not None and client.is_connected:
                    await client.stop()
            except Exception:
                logging.exception("Failed to stop %s client cleanly", label)
        record_service_event("bot_stopped", "تم إيقاف خدمات Telegram")
        logging.info("Services stopped")


def run_with_retries() -> None:
    """Retry transient Telegram startup failures without an endless crash loop."""
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            # Reuse Pyrogram's current loop instead of nesting asyncio.run().
            asyncio.get_event_loop().run_until_complete(main())
            return
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            record_service_event("bot_retry", f"المحاولة {attempt}/{max_attempts}: {type(exc).__name__}: {exc}")
            if attempt >= max_attempts:
                record_service_event("bot_failed", "توقفت المحاولات؛ راجع bot_process.log")
                raise
            time.sleep(min(5 * attempt, 15))


if __name__ == "__main__":
    run_with_retries()
