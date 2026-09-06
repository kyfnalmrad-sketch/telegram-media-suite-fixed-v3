from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message


@Client.on_message(filters.private & filters.command("start"))
async def start(_: Client, message: Message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("انزل الفيديو", callback_data="download_video")]
    ])
    await message.reply_text(
        "أهلًا بك. اضغط «انزل الفيديو» ثم أرسل رابط رسالة Telegram.",
        reply_markup=keyboard,
    )


@Client.on_message(filters.private & filters.command("help"))
async def help_command(_: Client, message: Message):
    await message.reply_text(
        "اضغط «انزل الفيديو» أو أرسل رابط رسالة Telegram مباشرة.\n"
        "يمكنك أيضًا استخدام: /save <رابط الرسالة>"
    )
