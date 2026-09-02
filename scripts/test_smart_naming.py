from types import SimpleNamespace
from downloader import message_file_name


def media_message(key, media_item, message_id=43):
    values = {item: None for item in ("document", "video", "audio", "voice", "video_note", "photo", "animation")}
    values[key] = media_item
    return SimpleNamespace(id=message_id, **values)


media = SimpleNamespace(file_name="Original Name.mp4", mime_type="video/mp4")
message = media_message("video", media, 42)
assert message_file_name(message, "عنوان الحلقة / الجزء الأول") == "Original Name.mp4"
assert message_file_name(message, "https://t.me/channel/123") == "Original Name.mp4"

for key, suffix in [("document", ".bin"), ("video", ".mp4"), ("audio", ".mp3"),
                    ("photo", ".jpg"), ("voice", ".ogg"), ("video_note", ".mp4"),
                    ("animation", ".mp4")]:
    media_item = SimpleNamespace(file_name=None, mime_type=None)
    name = message_file_name(media_message(key, media_item), "وصف خارجي")
    assert name == f"{key}{suffix}", (key, name)

print("SMART_NAMING_OK")


import asyncio
from downloader import TelegramSession


class FakeClient:
    def __init__(self, messages):
        self.messages = messages

    async def get_messages(self, chat_id, message_id):
        del chat_id
        return self.messages.get(message_id)


file_one = media_message("document", SimpleNamespace(file_name="part-one.pdf", mime_type="application/pdf"), 44)
file_one.empty = False
file_two = media_message("video", SimpleNamespace(file_name="part-two.mp4", mime_type="video/mp4"), 45)
file_two.empty = False
text_after = SimpleNamespace(id=46, empty=False, text="وصف الحلقة بعد الملفات", caption=None,
                             document=None, video=None, audio=None, voice=None,
                             video_note=None, photo=None, animation=None)
base = SimpleNamespace(id=43, empty=False, text=None, caption=None, chat=SimpleNamespace(id=99))
worker = object.__new__(TelegramSession)
worker.client = FakeClient({44: file_one, 45: file_two, 46: text_after})
assert asyncio.run(worker._nearby_label(base)) == "part-one — part-two — وصف الحلقة بعد الملفات"
print("NEARBY_CONTEXT_OK")
