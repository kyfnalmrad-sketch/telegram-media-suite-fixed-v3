from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("انزل الوسائط", callback_data="download_video")],
        [InlineKeyboardButton("📋 العمليات", callback_data="queue:list")],
        [InlineKeyboardButton("⏸ إيقاف الجديد", callback_data="queue:pause"), InlineKeyboardButton("▶️ استئناف", callback_data="queue:resume")],
    ])


@Client.on_message(filters.private & filters.command("start"))
async def start(_: Client, message: Message):
    await message.reply_text(
        "أهلًا بك. أرسل رابط Telegram مباشرة أو اضغط «انزل الوسائط».\n"
        "يدعم الفيديو والصوت والصور والمستندات وPDF، ويعالج الروابط بالتسلسل.",
        reply_markup=menu(),
    )


@Client.on_message(filters.private & filters.command("help"))
async def help_command(_: Client, message: Message):
    await message.reply_text(
        "أرسل رابط رسالة Telegram مباشرة أو استخدم /save <الرابط>.\n"
        "الملف يصل كرسالة Telegram عادية لتشاهده أو تحفظه من جهازك.\n"
        "زر «إيقاف الجديد» يوقف بدء عمليات جديدة، والاستئناف يعيد الطابور.",
        reply_markup=menu(),
    )
