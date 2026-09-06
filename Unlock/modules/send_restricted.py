from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlparse

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from .UserBot.saver import cleanup, saver

LINK_RE = re.compile(r"https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s<>]+", re.I)
def parse_message_link(link: str) -> tuple[str | int, int] | None:
    value = link.strip().strip("<>\"'").rstrip(".,،؛:!?؟)]}>")
    parsed = urlparse(value)
    if parsed.netloc.lower().removeprefix("www.") not in {"t.me", "telegram.me", "telegram.dog"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0].lower() == "s":
        parts = parts[1:]
    if len(parts) >= 3 and parts[0].lower() == "c" and parts[1].isdigit() and parts[-1].isdigit():
        return int(f"-100{parts[1]}"), int(parts[-1])
    if len(parts) >= 2 and parts[0].lower() not in {"c", "joinchat"} and parts[-1].isdigit():
        return parts[0].lstrip("@"), int(parts[-1])
    return None


async def deliver(bot: Client, message: Message, link: str) -> None:
    parsed = parse_message_link(link)
    if not parsed:
        await message.reply_text("الرابط غير صحيح. أرسل رابط رسالة Telegram مثل:\nhttps://t.me/channel/123")
        return
    chat_id, message_id = parsed
    status = await message.reply_text("⏳ جارٍ جلب الفيديو وإرساله إلى هذه المحادثة...\n[□□□□□□□□□□] 0%")
    result = await saver(message, chat_id, message_id, status)
    if not result:
        return
    try:
        caption = result.get("caption")
        source = result.get("source") or "مصدر غير معروف"
        details = f"\n\nالمصدر: {source}\nرقم الرسالة: {message_id}"
        caption = ((caption or "بدون وصف") + details)[:1024]
        last_update = 0.0

        async def upload_progress(current: int, total: int):
            nonlocal last_update
            now = time.monotonic()
            if now - last_update < 2 and current < total:
                return
            last_update = now
            percent = int(current * 100 / total) if total else 0
            filled = min(10, percent // 10)
            bar = "■" * filled + "□" * (10 - filled)
            await status.edit_text(f"📤 جارٍ إرسال الفيديو إلى البوت...\n[{bar}] {percent}%")

        if result["type"] == "video":
            await bot.send_video(message.chat.id, result["path"], caption=caption, supports_streaming=True, progress=upload_progress)
        else:
            await bot.send_document(message.chat.id, result["path"], caption=caption, progress=upload_progress)
        await status.delete()
    except Exception as exc:
        await status.edit_text(f"تعذر إرسال الملف: {type(exc).__name__}")
    finally:
        await cleanup(result)


@Client.on_callback_query(filters.regex("^download_video$"))
async def download_button(_: Client, query: CallbackQuery):
    await query.answer()
    await query.message.reply_text("أرسل رابط الفيديو من Telegram الآن.")


@Client.on_message(filters.private & filters.command("save"))
async def save_command(bot: Client, message: Message):
    text = message.text or ""
    link = text.partition(" ")[2].strip()
    if not link:
        await message.reply_text("أرسل الرابط بعد الأمر، أو اضغط زر «انزل الفيديو» ثم أرسل الرابط.")
        return
    await deliver(bot, message, link)


@Client.on_message(filters.private & filters.regex(r"https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/", re.I))
async def save_link(bot: Client, message: Message):
    match = LINK_RE.search(message.text or "")
    if match:
        await deliver(bot, message, match.group(0))
