from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlparse

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from .UserBot.saver import cleanup, inspect_media, saver
from .job_queue import queue
from .start import menu
from .ui_state import CONTROL_MESSAGES

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
    result = await saver(status, job["chat_ref"], job["message_id"], status, job_id=job["id"])
    if not result:
        if job.get("status") == "cancelled":
            return
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
            if job.get("status") == "cancelled":
                raise RuntimeError("تم إلغاء العملية")
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
        info = await inspect_media(parsed[0], parsed[1])
        if not info:
            await message.reply_text("الرابط لا يحتوي على فيديو أو صوت أو صورة أو مستند قابل للإرسال.")
            return
    except Exception as exc:
        await message.reply_text(f"تعذر تحليل الرابط: {type(exc).__name__}")
        return
    try:
        job = queue.add(message.chat.id, message.from_user.id if message.from_user else 0, link, parsed)
    except RuntimeError as exc:
        await message.reply_text(f"⚠️ {exc}")
        return
    size_mb = info["size"] / 1048576 if info["size"] else 0
    source = getattr(getattr(info["message"], "chat", None), "title", None) or getattr(getattr(info["message"], "chat", None), "username", None) or str(parsed[0])
    caption = (info.get("caption") or "بدون وصف")[:500]
    queue.update(job["id"], media_type=info["type"], size=info["size"], source=source, caption=caption)
    control_id = CONTROL_MESSAGES.get(message.chat.id)
    status = await bot.get_messages(message.chat.id, control_id) if control_id else None
    if not status or status.empty:
        status = await message.reply_text("لوحة العمليات", reply_markup=menu())
        CONTROL_MESSAGES[message.chat.id] = status.id
    await status.edit_text(
        f"🔎 تم تحليل العملية #{job['id']}\n"
        f"النوع: {info['type']}\nالحجم: {size_mb:.2f} MB\n"
        f"المصدر: {source}\nرقم الرسالة: {parsed[1]}\n"
        f"الوصف: {caption}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬇️ جلب إلى البوت", callback_data=f"job:fetch:{job['id']}")],
            [InlineKeyboardButton("🛑 إلغاء", callback_data=f"job:cancel:{job['id']}")],
        ]),
    )
    queue.update(job["id"], status_message_id=status.id)


@Client.on_callback_query(filters.regex(r"^job:fetch:(\d+)$"))
async def fetch_job(bot: Client, query: CallbackQuery):
    job_id = int(query.data.split(":")[-1])
    job = queue.get(job_id)
    if not job or job.get("status") != "ready":
        await query.answer("العملية ليست جاهزة للجلب")
        return
    queue.update(job_id, status="queued", phase="في قائمة الانتظار للجلب", progress=0, error="")
    await query.answer("بدأت عملية الجلب")
    await query.message.edit_text(
        f"📥 العملية #{job_id} في قائمة الانتظار للجلب...\n"
        "سيتم تحديث هذه الرسالة عند بدء النقل."
    )
    # Start/reuse the worker from the button itself. This also covers a
    # dashboard-started bot where the worker was not created during boot.
    await queue.start(lambda current: process_job(bot, current))


@Client.on_callback_query(filters.regex("^download_video$"))
async def download_button(_: Client, query: CallbackQuery):
    await query.answer()
    await query.message.edit_text(
        "أرسل رابط الوسائط من Telegram الآن. يدعم الفيديو والصوت والصور والمستندات وPDF.",
        reply_markup=menu(),
    )


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
        rows = []
        for job in jobs[:6]:
            buttons = []
            if job.get("status") not in {"completed", "cancelled"}:
                buttons.append(InlineKeyboardButton(f"🛑 إلغاء #{job['id']}", callback_data=f"job:cancel:{job['id']}"))
            if job.get("status") in {"failed", "cancelled"}:
                buttons.append(InlineKeyboardButton(f"🔁 إعادة #{job['id']}", callback_data=f"job:retry:{job['id']}"))
            if buttons:
                rows.append(buttons)
    await query.answer("تم")
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows) if action == "list" and rows else None)


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
