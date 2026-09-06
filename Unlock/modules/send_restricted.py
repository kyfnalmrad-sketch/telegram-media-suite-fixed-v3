from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlparse

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from .UserBot.saver import cleanup, saver
from .job_queue import queue

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


def progress_text(prefix: str, current: int, total: int) -> str:
    percent = int(current * 100 / total) if total else 0
    filled = min(10, percent // 10)
    return f"{prefix}\n[{('■' * filled) + ('□' * (10 - filled))}] {percent}%"


async def process_job(bot: Client, job: dict) -> None:
    status = None
    status_id = job.get("status_message_id")
    if status_id:
        status = await bot.get_messages(job["chat_id"], status_id)
    if not status or status.empty:
        status = await bot.send_message(job["chat_id"], f"⏳ العملية #{job['id']} قيد البدء...")
        queue.update(job["id"], status_message_id=status.id)
    queue.update(job["id"], phase="جاري جلب الوسائط من Telegram", status="downloading")
    result = await saver(status, job["chat_ref"], job["message_id"], status)
    if not result:
        queue.update(job["id"], status="failed", phase="فشل جلب الوسائط")
        return
    try:
        caption = result.get("caption") or "بدون وصف"
        source = result.get("source") or job["chat_ref"]
        details = f"\n\nالمصدر: {source}\nرقم الرسالة: {job['message_id']}\nرقم العملية: #{job['id']}"
        caption = (caption + details)[:1024]
        queue.update(job["id"], phase="جاري إرسال الوسائط إلى البوت", status="uploading", progress=0)
        last_update = 0.0

        def upload_progress(current: int, total: int):
            nonlocal last_update
            now = time.monotonic()
            if now - last_update < 2 and current < total:
                return
            last_update = now
            queue.update(job["id"], progress=int(current * 100 / total) if total else 0, current=current, total=total)
            asyncio.create_task(status.edit_text(progress_text("📤 جاري إرسال الوسائط إلى البوت...", current, total)))

        if result["type"] == "video":
            await bot.send_video(job["chat_id"], result["path"], caption=caption, supports_streaming=True, progress=upload_progress)
        elif result["type"] == "audio":
            await bot.send_audio(job["chat_id"], result["path"], caption=caption, progress=upload_progress)
        elif result["type"] == "photo":
            await bot.send_photo(job["chat_id"], result["path"], caption=caption)
        elif result["type"] == "voice":
            await bot.send_voice(job["chat_id"], result["path"], caption=caption, progress=upload_progress)
        else:
            await bot.send_document(job["chat_id"], result["path"], caption=caption, progress=upload_progress)
        queue.update(job["id"], status="completed", phase="اكتمل — جاهز للمشاهدة والتنزيل", progress=100)
        await status.edit_text(f"✅ اكتملت العملية #{job['id']}\nالوسائط جاهزة للمشاهدة أو الحفظ من رسالة Telegram.")
    finally:
        await cleanup(result)


async def enqueue_link(bot: Client, message: Message, link: str) -> None:
    parsed = parse_message_link(link)
    if not parsed:
        await message.reply_text("الرابط غير صحيح. أرسل رابط رسالة Telegram مثل:\nhttps://t.me/channel/123")
        return
    try:
        job = queue.add(message.chat.id, message.from_user.id if message.from_user else 0, link, parsed)
    except RuntimeError as exc:
        await message.reply_text(f"⚠️ {exc}")
        return
    status = await message.reply_text(f"📥 أضيفت العملية #{job['id']} إلى قائمة الانتظار.\nالحالة: في الانتظار")
    queue.update(job["id"], status_message_id=status.id)
    await queue.start(lambda current: process_job(bot, current))


@Client.on_callback_query(filters.regex("^download_video$"))
async def download_button(_: Client, query: CallbackQuery):
    await query.answer()
    await query.message.reply_text("أرسل رابط الوسائط من Telegram الآن. يدعم الفيديو والصوت والصور والمستندات وPDF.")


@Client.on_callback_query(filters.regex("^queue:(pause|resume|list)$"))
async def queue_control(_: Client, query: CallbackQuery):
    action = query.data.split(":", 1)[1]
    if action == "pause":
        queue.pause()
        text = "⏸ تم إيقاف بدء العمليات الجديدة. العملية الحالية تكمل حتى تنتهي."
    elif action == "resume":
        queue.resume()
        text = "▶️ تم استئناف قائمة الانتظار."
    else:
        jobs = queue.recent(10)
        text = "📋 العمليات:\n" + "\n".join(f"#{j['id']} — {j['phase']}" for j in jobs) if jobs else "لا توجد عمليات."
    await query.answer("تم")
    await query.message.reply_text(text)


@Client.on_callback_query(filters.regex(r"^job:(cancel|retry):(\d+)$"))
async def job_control(_: Client, query: CallbackQuery):
    action, raw_id = query.data.split(":")[1:]
    job_id = int(raw_id)
    ok = queue.cancel(job_id) if action == "cancel" else queue.retry(job_id)
    await query.answer("تم" if ok else "لا يمكن تنفيذ العملية")
    await query.message.reply_text(f"{'🛑 أُلغيَت' if action == 'cancel' else '🔁 أُعيدت'} العملية #{job_id}." if ok else "العملية غير متاحة.")


@Client.on_message(filters.private & filters.command("save"))
async def save_command(bot: Client, message: Message):
    link = (message.text or "").partition(" ")[2].strip()
    if not link:
        await message.reply_text("أرسل الرابط بعد الأمر، أو اضغط زر «انزل الوسائط» ثم أرسل الرابط.")
        return
    await enqueue_link(bot, message, link)


@Client.on_message(filters.private & filters.regex(r"https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/", re.I))
async def save_link(bot: Client, message: Message):
    for match in LINK_RE.findall(message.text or ""):
        await enqueue_link(bot, message, match)
