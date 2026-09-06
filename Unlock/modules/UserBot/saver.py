from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from pyrogram.enums import MessageMediaType
from pyrogram.errors import ChannelInvalid, ChannelPrivate, ChatIdInvalid, FloodWait, PeerIdInvalid
from pyrogram.types import Message

from ... import DOWNLOAD_DIR, MAX_ALLOWED_DOWNLOAD_SIZE, ubot


def bytes_to_mb(value: int) -> float:
    return value / 1048576


def media_info(message: Message) -> tuple[str | None, int]:
    mapping = {
        MessageMediaType.PHOTO: ("photo", lambda item: getattr(item.photo, "file_size", 0)),
        MessageMediaType.VIDEO: ("video", lambda item: getattr(item.video, "file_size", 0)),
        MessageMediaType.AUDIO: ("audio", lambda item: getattr(item.audio, "file_size", 0)),
        MessageMediaType.VOICE: ("voice", lambda item: getattr(item.voice, "file_size", 0)),
        MessageMediaType.DOCUMENT: ("document", lambda item: getattr(item.document, "file_size", 0)),
        MessageMediaType.VIDEO_NOTE: ("video_note", lambda item: getattr(item.video_note, "file_size", 0)),
    }
    entry = mapping.get(message.media)
    if not entry:
        return None, 0
    kind, size_getter = entry
    try:
        return kind, int(size_getter(message))
    except Exception:
        return kind, 0


async def _get_message_with_refresh(chat_id: int | str, msg_id: int) -> Message:
    try:
        return await ubot.get_messages(chat_id, msg_id)
    except (PeerIdInvalid, ChannelInvalid, ChatIdInvalid):
        wanted_id = int(chat_id) if str(chat_id).lstrip("-").isdigit() else None
        wanted_username = str(chat_id).lstrip("@").casefold()
        async for dialog in ubot.get_dialogs():
            chat = getattr(dialog, "chat", None)
            if not chat:
                continue
            username = str(getattr(chat, "username", "") or "").lstrip("@").casefold()
            if getattr(chat, "id", None) == wanted_id or (wanted_username and username == wanted_username):
                return await ubot.get_messages(chat.id, msg_id)
        raise


async def inspect_media(chat_id: int | str, msg_id: int) -> dict[str, Any] | None:
    """Read media metadata only; do not download the file."""
    msg = await _get_message_with_refresh(chat_id, int(msg_id))
    if not msg or msg.empty:
        return None
    file_type, size = media_info(msg)
    if not file_type:
        return None
    return {
        "message": msg,
        "type": file_type,
        "size": size,
        "caption": (msg.caption or msg.text or "").strip() or None,
    }


async def saver(m: Message, chat_id: int | str, msg_id: int, processing_msg: Message | None = None) -> dict[str, Any] | None:
    try:
        msg = await _get_message_with_refresh(chat_id, int(msg_id))
    except ChannelPrivate:
        if processing_msg:
            await processing_msg.edit_text("لا يستطيع حساب الجلسة الوصول إلى هذه القناة.")
        return None
    except Exception as exc:
        logging.exception("Failed to fetch Telegram message")
        if processing_msg:
            await processing_msg.edit_text(f"تعذر قراءة الرسالة: {type(exc).__name__}")
        return None

    if not msg or msg.empty:
        if processing_msg:
            await processing_msg.edit_text("الرسالة غير موجودة أو تم حذفها.")
        return None
    file_type, size = media_info(msg)
    if not file_type:
        if processing_msg:
            await processing_msg.edit_text("الرابط لا يحتوي على فيديو أو ملف وسائط قابل للتنزيل.")
        return None
    if MAX_ALLOWED_DOWNLOAD_SIZE is not None and bytes_to_mb(size) > MAX_ALLOWED_DOWNLOAD_SIZE:
        if processing_msg:
            await processing_msg.edit_text(f"حجم الملف أكبر من الحد المسموح ({MAX_ALLOWED_DOWNLOAD_SIZE:g} MB).")
        return None

    target = DOWNLOAD_DIR / f"{m.from_user.id}_{msg.id}"
    target.mkdir(parents=True, exist_ok=True)
    file_path = None
    try:
        file_path = await ubot.download_media(msg, file_name=str(target / ""))
        if not file_path or not os.path.exists(file_path):
            raise RuntimeError("Telegram لم يرجع ملف التنزيل")
        caption = (msg.caption or msg.text or "").strip() or None
        return {"message": msg, "path": str(file_path), "type": file_type, "caption": caption}
    except FloodWait as exc:
        if processing_msg:
            await processing_msg.edit_text(f"Telegram طلب الانتظار {exc.value} ثانية ثم أعد المحاولة.")
        return None
    except Exception as exc:
        logging.exception("Failed to download media")
        if processing_msg:
            await processing_msg.edit_text(f"تعذر تنزيل الوسيط: {type(exc).__name__}")
        return None


async def cleanup(result: dict[str, Any] | None) -> None:
    if not result:
        return
    try:
        Path(result["path"]).unlink(missing_ok=True)
        parent = Path(result["path"]).parent
        if parent != DOWNLOAD_DIR:
            parent.rmdir()
    except OSError:
        pass
