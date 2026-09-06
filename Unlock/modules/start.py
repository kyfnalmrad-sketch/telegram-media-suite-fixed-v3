from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .ui_state import CONTROL_MESSAGES


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 جلب وسائط", callback_data="download_video")],
        [InlineKeyboardButton("📋 العمليات", callback_data="queue:list"), InlineKeyboardButton("📊 حالة الطابور", callback_data="queue:status")],
        [InlineKeyboardButton("⏸ إيقاف الجديد", callback_data="queue:pause"), InlineKeyboardButton("▶️ استئناف", callback_data="queue:resume")],
    ])


@Client.on_message(filters.private & filters.command("start"))
async def start(_: Client, message: Message):
    control = await message.reply_text(
        "أهلًا بك. هذه لوحة التحكم.\n"
        "أرسل رابط Telegram مباشرة أو اضغط «📥 جلب وسائط».\n"
        "يدعم الفيديو والصوت والصور والمستندات وPDF، ويعالج الروابط بالتسلسل.\n"
        "ستظهر نسبة الجلب والإرسال داخل رسالة العملية نفسها.",
        reply_markup=menu(),
    )
    CONTROL_MESSAGES[message.chat.id] = control.id


@Client.on_message(filters.private & filters.command("help"))
async def help_command(bot: Client, message: Message):
    control_id = CONTROL_MESSAGES.get(message.chat.id)
    if control_id:
        try:
            await bot.edit_message_text(
                message.chat.id,
                control_id,
                "أرسل رابط رسالة Telegram مباشرة أو استخدم /save <الرابط>.\n"
                "سيصل الملف كرسالة Telegram عادية لتشاهده أو تحفظه من جهازك.",
                reply_markup=menu(),
            )
            return
        except Exception:
            pass
    control = await message.reply_text("أرسل رابط Telegram مباشرة.", reply_markup=menu())
    CONTROL_MESSAGES[message.chat.id] = control.id
