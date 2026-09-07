from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from .ui_state import CONTROL_MESSAGES, update_control


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 جلب وسائط", callback_data="download_video")],
        [InlineKeyboardButton("📋 العمليات", callback_data="queue:list"), InlineKeyboardButton("📊 الحالة", callback_data="queue:status")],
        [InlineKeyboardButton("🧾 السجلات", callback_data="queue:logs"), InlineKeyboardButton("🔄 تحديث", callback_data="queue:refresh")],
        [InlineKeyboardButton("⏸ إيقاف الجديد", callback_data="queue:pause"), InlineKeyboardButton("▶️ استئناف", callback_data="queue:resume")],
        [InlineKeyboardButton("❓ مساعدة", callback_data="queue:help")],
    ])


@Client.on_message(filters.private & filters.command("start"))
async def start(bot: Client, message: Message):
    # Older releases displayed a persistent reply keyboard. Remove it first;
    # the current UI intentionally uses only inline buttons in the control
    # message, so both layouts do not remain visible at the same time.
    await bot.send_message(message.chat.id, "تم تحديث قائمة البوت.", reply_markup=ReplyKeyboardRemove())
    await update_control(
        bot,
        message.chat.id,
        "أهلًا بك في لوحة التحكم.\nأرسل رابط Telegram مباشرة أو اختر قسمًا من القائمة.\nستظهر حالة الجلب والإرسال والسجل في نفس رسالة التحكم.",
        menu(),
    )


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
    await update_control(bot, message.chat.id, "أرسل رابط Telegram مباشرة أو اختر قسمًا من القائمة.", menu())
