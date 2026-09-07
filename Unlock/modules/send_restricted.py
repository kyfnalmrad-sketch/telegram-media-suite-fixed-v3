from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from .UserBot.saver import cleanup, inspect_media, saver
from .job_queue import queue
from .media_upload import resolve_upload_path
from .progress import ProgressReporter
from .errors import explain_error
from .start import menu
from .service_log import record_service_event
from .ui_state import CONTROL_MESSAGES, edit_callback_control

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


async def process_job(bot: Client, job: dict) -> None:
    status = None
    status_id = job.get("status_message_id")
    if status_id:
        status = await bot.get_messages(job["chat_id"], status_id)
    if not status or status.empty:
        status = await bot.send_message(job["chat_id"], f"⏳ العملية #{job['id']} قيد البدء...")
        queue.update(job["id"], status_message_id=status.id)
    queue.update(job["id"], phase="جاري جلب الوسائط من Telegram", status="downloading")
    download_reporter = ProgressReporter(status, job["id"])
    result = await saver(
        status,
        job["chat_ref"],
        job["message_id"],
        status,
        job_id=job["id"],
        progress_reporter=download_reporter,
    )
    if not result:
        if job.get("status") == "cancelled":
            return
        details = job.get("error") or "تعذر جلب الوسائط من Telegram."
        solution = job.get("error_solution") or "اضغط إعادة المحاولة بعد التأكد من وصول الحساب إلى القناة."
        queue.update(job["id"], status="failed", phase="فشل جلب الوسائط", event=f"السبب: {details}")
        await download_reporter.finish(f"❌ فشلت العملية #{job['id']} أثناء الجلب\nالسبب: {details}\nالحل المقترح: {solution}")
        return
    try:
        caption = result.get("caption") or "بدون وصف"
        source = result.get("source") or job["chat_ref"]
        details = f"\n\nالمصدر: {source}\nرقم الرسالة: {job['message_id']}\nرقم العملية: #{job['id']}"
        caption = (caption + details)[:1024]
        queue.update(job["id"], phase="جاري إرسال الوسائط إلى البوت", status="uploading", progress=0, current=0, total=0, speed=0)
        upload_path = resolve_upload_path(result)
        upload_reporter = ProgressReporter(status, job["id"])

        def upload_progress(current: int, total: int):
            if job.get("status") == "cancelled":
                raise RuntimeError("تم إلغاء العملية")
            upload_reporter.update_sync("📤 جاري إرسال الوسائط إلى البوت...", current, total)

        # Use an open handle so Pyrogram cannot mistake an unavailable path
        # for a Telegram file id. The handle also keeps the file available for
        # the complete upload, including large files on Render's /tmp volume.
        with upload_path.open("rb") as media_file:
            if result["type"] == "video":
                await bot.send_video(job["chat_id"], media_file, caption=caption, file_name=upload_path.name, supports_streaming=True, progress=upload_progress)
            elif result["type"] == "audio":
                await bot.send_audio(job["chat_id"], media_file, caption=caption, file_name=upload_path.name, progress=upload_progress)
            elif result["type"] == "photo":
                await bot.send_photo(job["chat_id"], media_file, caption=caption)
            elif result["type"] == "voice":
                await bot.send_voice(job["chat_id"], media_file, caption=caption, progress=upload_progress)
            else:
                await bot.send_document(job["chat_id"], media_file, caption=caption, file_name=upload_path.name, progress=upload_progress)
        await upload_reporter.finish(
            f"✅ اكتملت العملية #{job['id']}\nالوسائط جاهزة للمشاهدة أو الحفظ من رسالة Telegram.",
            status="completed",
            phase="اكتمل — جاهز للمشاهدة والتنزيل",
            progress=100,
        )
    except Exception as exc:
        code, details, solution = explain_error(exc, "الإرسال")
        queue.update(job["id"], status="failed", phase="فشل إرسال الوسائط", error_code=code, error=details, error_message=details, error_solution=solution, event=f"الإرسال: {details}")
        reporter = locals().get("upload_reporter") or download_reporter
        await reporter.finish(f"❌ فشلت العملية #{job['id']} أثناء الإرسال\nالسبب: {details}\nالحل المقترح: {solution}", status="failed", phase="فشل إرسال الوسائط")
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
        logging.exception("Failed to inspect Telegram link")
        record_service_event(
            "job_inspection_failed",
            f"chat={parsed[0]} message={parsed[1]} error={type(exc).__name__}: {exc}",
        )
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
    # Keep the main control menu responsive and create a dedicated message for
    # this transfer. Progress edits must not overwrite the menu.
    progress_message = await bot.send_message(
        query.message.chat.id,
        f"📥 العملية #{job_id} في قائمة الانتظار للجلب...\n"
        "ستظهر هنا مراحل التحليل والجلب والإرسال.",
    )
    queue.update(job_id, status_message_id=progress_message.id, event="تم إنشاء رسالة تقدم مستقلة")
    try:
        await query.message.edit_text("لوحة التحكم جاهزة لعملية أخرى.", reply_markup=menu())
    except Exception:
        logging.exception("Failed to refresh control menu after starting job")
    # Start/reuse the worker from the button itself. This also covers a
    # dashboard-started bot where the worker was not created during boot.
    await queue.start(lambda current: process_job(bot, current))


def operation_buttons(job: dict) -> list[list[InlineKeyboardButton]]:
    job_id = job["id"]
    status = job.get("status")
    rows: list[list[InlineKeyboardButton]] = []
    if status == "ready":
        rows.append([InlineKeyboardButton(f"⬇️ جلب #{job_id}", callback_data=f"job:fetch:{job_id}")])
    elif status in {"queued", "processing", "downloading", "uploading", "paused"}:
        rows.append([InlineKeyboardButton(f"🛑 إلغاء #{job_id}", callback_data=f"job:cancel:{job_id}")])
    elif status in {"failed", "cancelled"}:
        rows.append([InlineKeyboardButton(f"🔁 إعادة #{job_id}", callback_data=f"job:retry:{job_id}")])
    rows.append([InlineKeyboardButton(f"🔎 تفاصيل #{job_id}", callback_data=f"job:view:{job_id}")])
    rows.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="queue:refresh")])
    return rows


def operation_text(job: dict, detailed: bool = False) -> str:
    status = job.get("status", "unknown")
    progress = int(job.get("progress") or 0)
    current = int(job.get("current") or 0)
    total = int(job.get("total") or job.get("size") or 0)
    speed = int(job.get("speed") or 0)
    status_names = {
        "ready": "جاهز للجلب", "queued": "في الانتظار", "processing": "قيد المعالجة",
        "downloading": "جاري الجلب", "uploading": "جاري الإرسال", "completed": "اكتمل",
        "failed": "فشل", "cancelled": "أُلغي",
    }
    lines = [
        f"العملية #{job['id']} — {status_names.get(status, status)}",
        f"المرحلة: {job.get('phase') or 'غير محددة'}",
        f"التقدم: {progress}%",
    ]
    if total:
        lines.append(f"الحجم: {format_bytes(current)} / {format_bytes(total)}")
    if speed:
        lines.append(f"السرعة: {format_bytes(speed)}/ث")
    if job.get("error"):
        lines.extend([f"السبب: {job['error']}", f"الحل: {job.get('error_solution') or 'أعد المحاولة بعد التحقق من المصدر.'}"])
    if detailed:
        events = job.get("events") or []
        if events:
            lines.append("\nآخر الأحداث:")
            lines.extend(f"• {event}" for event in events[-8:])
    return "\n".join(lines)[:3900]


def queue_status_text() -> str:
    jobs = queue.recent(100)
    names = ("ready", "queued", "downloading", "uploading", "completed", "failed", "cancelled")
    counts = {name: sum(1 for job in jobs if job.get("status") == name) for name in names}
    state = "متوقفة مؤقتًا" if not queue.pause_event.is_set() else "تعمل"
    return (
        "📊 حالة الطابور\n"
        f"الحالة: {state}\n"
        f"الجاهز: {counts['ready']} | المنتظر: {counts['queued']}\n"
        f"الجلب: {counts['downloading']} | الإرسال: {counts['uploading']}\n"
        f"المكتمل: {counts['completed']} | الفاشل: {counts['failed']} | الملغى: {counts['cancelled']}"
    )


@Client.on_callback_query(filters.regex("^download_video$"))
async def download_button(_: Client, query: CallbackQuery):
    await query.answer()
    await query.message.edit_text(
        "أرسل رابط الوسائط من Telegram الآن. يدعم الفيديو والصوت والصور والمستندات وPDF.",
        reply_markup=menu(),
    )


@Client.on_callback_query(filters.regex(r"^queue:(pause|resume|list|status|logs|refresh|help)$"))
async def queue_control(bot: Client, query: CallbackQuery):
    action = query.data.split(":", 1)[1]
    if action == "pause":
        queue.pause()
        text = "⏸ تم إيقاف بدء العمليات الجديدة. العملية الحالية تكمل حتى تنتهي."
        markup = menu()
    elif action == "resume":
        queue.resume()
        text = "▶️ تم استئناف قائمة الانتظار."
        markup = menu()
    elif action == "status":
        text = queue_status_text()
        markup = menu()
    elif action == "logs":
        events = []
        for job in queue.recent(10):
            for event in (job.get("events") or [])[-3:]:
                events.append(f"#{job['id']} — {event}")
        text = "🧾 السجلات الأخيرة\n\n" + "\n".join(events[-24:]) if events else "🧾 لا توجد سجلات بعد."
        markup = menu()
    elif action == "help":
        text = "❓ المساعدة\n\nأرسل رابط رسالة Telegram أو استخدم زر «📥 جلب وسائط». ثم راجع «📋 العمليات» لمتابعة الجلب والإرسال، ويمكنك الإلغاء أو إعادة المحاولة من تفاصيل العملية."
        markup = menu()
    elif action == "refresh":
        text = queue_status_text()
        markup = menu()
    else:
        jobs = queue.recent(10)
        text = "📋 العمليات\n\n" + "\n\n".join(operation_text(job) for job in jobs) if jobs else "📋 لا توجد عمليات."
        rows = []
        for job in jobs[:8]:
            rows.extend(operation_buttons(job))
        rows.append([InlineKeyboardButton("🔄 تحديث", callback_data="queue:list"), InlineKeyboardButton("🏠 الرئيسية", callback_data="download_video")])
        markup = InlineKeyboardMarkup(rows)
    await query.answer("تم")
    await edit_callback_control(query, text[:3900], markup)


@Client.on_callback_query(filters.regex(r"^job:view:(\d+)$"))
async def job_details(_: Client, query: CallbackQuery):
    job_id = int(query.data.split(":")[-1])
    job = queue.get(job_id)
    await query.answer("تم")
    if not job:
        await edit_callback_control(query, "العملية غير موجودة.", menu())
        return
    await edit_callback_control(query, operation_text(job, detailed=True), InlineKeyboardMarkup(operation_buttons(job)))


@Client.on_callback_query(filters.regex(r"^job:(cancel|retry):(\d+)$"))
async def job_control(bot: Client, query: CallbackQuery):
    action, raw_id = query.data.split(":")[1:]
    job_id = int(raw_id)
    ok = queue.cancel(job_id) if action == "cancel" else queue.retry(job_id)
    if ok and action == "retry":
        await queue.start(lambda current: process_job(bot, current))
    await query.answer("تم" if ok else "لا يمكن تنفيذ العملية")
    job = queue.get(job_id)
    await edit_callback_control(query, operation_text(job) if job else "العملية غير متاحة.", InlineKeyboardMarkup(operation_buttons(job)) if job else menu())


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
