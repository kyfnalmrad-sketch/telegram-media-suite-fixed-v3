from __future__ import annotations

from pyrogram.errors import ChannelPrivate, FloodWait, MessageIdInvalid, PeerIdInvalid


def explain_error(exc: Exception, phase: str = "المعالجة") -> tuple[str, str, str]:
    name = type(exc).__name__
    raw = str(exc).strip()
    if isinstance(exc, ChannelPrivate):
        return "CHANNEL_PRIVATE", "القناة خاصة أو الحساب غير مشترك فيها.", "افتح القناة بالحساب المرتبط بالجلسة ثم أعد المحاولة."
    if isinstance(exc, FloodWait):
        return "TELEGRAM_FLOOD_WAIT", f"Telegram طلب الانتظار {exc.value} ثانية.", "انتظر المدة ثم أعد المحاولة."
    if isinstance(exc, (PeerIdInvalid, MessageIdInvalid)):
        return "MESSAGE_ACCESS", "تعذر الوصول إلى القناة أو رقم الرسالة.", "تأكد من الرابط وأن الحساب المرتبط يستطيع رؤية القناة والرسالة."
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return "NETWORK_ERROR", "انقطع الاتصال أثناء العملية.", "أعد المحاولة؛ سيتم استخدام مسار جلب بديل."
    if "FILE_REFERENCE" in name.upper() or "file reference" in raw.lower():
        return "FILE_REFERENCE_EXPIRED", "مرجع الملف القديم انتهت صلاحيته.", "أعد جلب الرسالة بمرجع حديث ثم أعد التنزيل."
    if phase == "الجلب":
        return "DOWNLOAD_ERROR", raw or "تعذر جلب الوسائط من Telegram.", "تأكد من وصول الحساب إلى الرسالة ثم اضغط إعادة المحاولة."
    if phase == "الإرسال":
        return "UPLOAD_ERROR", raw or "تعذر إرسال الوسائط إلى البوت.", "تحقق من اتصال Render وحجم الملف ثم أعد المحاولة."
    return name.upper(), raw or "حدث خطأ غير معروف.", "راجع التفاصيل ثم أعد المحاولة."
