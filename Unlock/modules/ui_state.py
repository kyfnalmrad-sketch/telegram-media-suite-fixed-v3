from __future__ import annotations

from pyrogram.types import InlineKeyboardMarkup

# One editable control/status message per private chat.
CONTROL_MESSAGES: dict[int, int] = {}


async def update_control(bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    """Edit the single control message, recreating it only when it is gone."""
    control_id = CONTROL_MESSAGES.get(chat_id)
    if control_id:
        try:
            control = await bot.get_messages(chat_id, control_id)
            if control and not control.empty:
                await control.edit_text(text, reply_markup=reply_markup)
                return control
        except Exception:
            pass
    control = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    CONTROL_MESSAGES[chat_id] = control.id
    return control


async def edit_callback_control(query, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    """Edit the callback's message without creating a new message."""
    try:
        await query.message.edit_text(text, reply_markup=reply_markup)
        CONTROL_MESSAGES[query.message.chat.id] = query.message.id
        return query.message
    except Exception:
        bot = getattr(query, "_client", None) or getattr(query.message, "_client", None)
        if bot is None:
            raise
        return await update_control(bot, query.message.chat.id, text, reply_markup)


__all__ = ["CONTROL_MESSAGES", "update_control", "edit_callback_control"]
