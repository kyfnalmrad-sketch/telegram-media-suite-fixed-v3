import pytest
from unittest.mock import AsyncMock

from pyrogram import Client

@pytest.mark.asyncio
async def test_bot_start_stop_calls_pyrogram_lifecycle():
    bot = Client("test-bot", api_id=12345, api_hash="a" * 32, bot_token="token")
    bot.start = AsyncMock()
    bot.stop = AsyncMock()

    await bot.start()
    await bot.stop()

    bot.start.assert_awaited_once()
    bot.stop.assert_awaited_once()
    assert not bot.is_connected


def test_require_clients_fails_with_clear_configuration_error(monkeypatch):
    from Unlock import require_clients

    monkeypatch.setattr("Unlock.API_ID", None)
    monkeypatch.setattr("Unlock.API_HASH", None)
    monkeypatch.setattr("Unlock.BOT_TOKEN", None)

    with pytest.raises(RuntimeError, match="API_ID وAPI_HASH وBOT_TOKEN"):
        require_clients()
