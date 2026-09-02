from __future__ import annotations

from types import SimpleNamespace

from bot_service import TelegramBotService


service = TelegramBotService(lambda: None, lambda: "/tmp", lambda text: None)

with_username = SimpleNamespace(
    from_user=SimpleNamespace(id=123456, first_name="أحمد", last_name="علي", username="ahmad_test"),
    sender_chat=None,
)
info = service._author_info(with_username)
assert info is not None
assert info["name"] == "أحمد علي"
assert info["id"] == 123456
assert info["url"] == "https://t.me/ahmad_test"

without_username = SimpleNamespace(
    from_user=SimpleNamespace(id=654321, first_name="بدون", last_name=None, username=None),
    sender_chat=None,
)
info = service._author_info(without_username)
assert info is not None
assert info["url"] == "tg://user?id=654321"

hidden = SimpleNamespace(from_user=None, sender_chat=None)
assert service._author_info(hidden) is None
print("AUTHOR_LOOKUP_TEST_OK")
