import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from bot_service import ensure_peer_resolved  # noqa: E402


class FakeClient:
    async def get_chat(self, _ref):
        raise RuntimeError("direct lookup unavailable")

    async def get_dialogs(self):
        yield SimpleNamespace(chat=SimpleNamespace(id=-100123, username="my_channel"))

    async def resolve_peer(self, chat_id):
        assert chat_id == -100123


async def check_resolution():
    by_username = await ensure_peer_resolved(FakeClient(), "@my_channel")
    assert by_username.id == -100123
    by_numeric_id = await ensure_peer_resolved(FakeClient(), -100123)
    assert by_numeric_id.id == -100123


if __name__ == "__main__":
    asyncio.run(check_resolution())
    print("PEER_RESOLUTION_OK")
else:
    asyncio.run(check_resolution())


def test_peer_resolution_from_dialogs():
    asyncio.run(check_resolution())
