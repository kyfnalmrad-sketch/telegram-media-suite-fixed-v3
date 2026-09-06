from __future__ import annotations

import asyncio
import re
import secrets
from urllib.parse import urlparse

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from .UserBot.saver import cleanup, inspect_media, saver

LINK_RE = re.compile(r"https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s<>]+", re.I)
PENDING: dict[str, tuple[str | int, int]] = {}


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
    status = await message.reply_text("⏳ جارٍ قراءة معلومات الفيديو فقط...")
    try:
        info = await inspect_media(chat_id, message_id)
    except Exception as exc:
        await status.edit_text(f"تعذر قراءة الرابط: {type(exc).__name__}")
        return
    if not info:
        await status.edit_text("الرابط لا يحتوي على فيديو أو ملف وسائط قابل للتنزيل.")
        return
    token = secrets.token_urlsafe(8)
    PENDING[token] = (chat_id, message_id)
    size_mb = info["size"] / 1048576 if info["size"] else 0
    caption = info.get("caption") or "بدون وصف"
    await status.edit_text(
        f"🎬 تم العثور على {info['type']}\n"
        f"📦 الحجم: {size_mb:.2f} MB\n"
        f"📝 الوصف: {caption[:800]}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("تنزيل الفيديو", callback_data=f"download:{token}")]]),
    )


@Client.on_callback_query(filters.regex(r"^download:([A-Za-z0-9_-]+)$"))
async def download_pending(bot: Client, query: CallbackQuery):
    await query.answer("سيبدأ التنزيل الآن")
    token = query.data.split(":", 1)[1]
    pending = PENDING.pop(token, None)
    if not pending:
        await query.message.reply_text("انتهت صلاحية هذا الرابط. أرسل الرابط من جديد.")
        return
    chat_id, message_id = pending
    status = await query.message.reply_text("⏳ جارٍ تنزيل الفيديو وإرساله...")
    result = await saver(query.message, chat_id, message_id, status)
    if not result:
        return
    try:
        caption = result.get("caption")
        if result["type"] == "video":
            await bot.send_video(query.message.chat.id, result["path"], caption=caption, supports_streaming=True)
        else:
            await bot.send_document(query.message.chat.id, result["path"], caption=caption)
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
