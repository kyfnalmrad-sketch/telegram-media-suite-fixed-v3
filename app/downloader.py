from __future__ import annotations

import asyncio
import os
import re
import threading
import time

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded, RPCError

TELEGRAM_HOSTS = {
    "t.me", "telegram.me", "www.t.me", "www.telegram.me", "telegram.dog",
    "www.telegram.dog", "telesco.pe", "www.telesco.pe",
}
URL_RE = re.compile(
    r"(?i)(?:(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog|telesco\.pe)/[^\s<>\[\]{}]+|"
    r"tg://(?:resolve|privatepost|msg_url|openmessage|join)\?[^\s<>\[\]{}]+)"
)


def _proxy_from_env() -> dict[str, Any] | None:
    """Build a Pyrogram proxy config from TMD_PROXY_URL when configured."""
    raw = os.environ.get("TMD_PROXY_URL", "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"socks5", "socks5h", "http", "https"} or not parsed.hostname or not parsed.port:
        raise ValueError("TMD_PROXY_URL يجب أن يكون socks5:// أو http:// مع المضيف والمنفذ")
    proxy: dict[str, Any] = {
        "scheme": "socks5" if parsed.scheme.lower() == "socks5h" else parsed.scheme.lower(),
        "hostname": parsed.hostname,
        "port": parsed.port,
    }
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy
URL_START_RE = re.compile(
    r"(?i)(?:"
    r"(?=https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog|telesco\.pe)/)|"
    r"(?<![A-Za-z0-9_/:])(?=(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog|telesco\.pe)/)|"
    r"(?=tg://(?:resolve|privatepost|msg_url|openmessage|join)\?)"
    r")"
)


class TelegramSession:
    """Own a Pyrogram user client on one event-loop thread."""

    def __init__(self, api_id: str, api_hash: str, session_path: str,
                 log: Callable[[str], None]):
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.session_path = str(Path(session_path).expanduser())
        self.log = log
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client: Client | None = None
        self.proxy = _proxy_from_env()
        self.thread: threading.Thread | None = None
        self.state = "starting"
        self.pending_phone = ""
        self.pending_hash = ""
        self.error = ""
        self.lock = threading.Lock()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        Path(self.session_path).expanduser().mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._thread_main, daemon=True, name="telegram-user-session")
        self.thread.start()

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.log("بدأ خيط جلسة Telegram.")
        try:
            self.client = Client(
                "tmd_user",
                api_id=self.api_id,
                api_hash=self.api_hash,
                workdir=self.session_path,
                no_updates=True,
                proxy=self.proxy,
            )
            self.log("اكتملت تهيئة عميل جلسة Telegram.")
            session_file = Path(self.session_path) / "tmd_user.session"
            if not session_file.exists():
                # لا ننتظر اتصالًا شبكيًا قبل أن يطلب المالك بدء التسجيل من /start.
                # الاتصال الأول وإنشاء ملف الجلسة يحدثان داخل _send_code.
                self._set_state("phone", "")
                self.log("لا توجد جلسة محفوظة؛ تنتظر جلسة Telegram رقم الهاتف من المالك.")
            else:
                self.log("توجد جلسة محفوظة؛ يبدأ الاتصال الآن.")
                self.loop.run_until_complete(self._connect())
            self.loop.run_forever()
        except Exception as exc:
            self._set_state("error", str(exc))
            self.log(f"فشل تشغيل جلسة Telegram: {type(exc).__name__}")
        finally:
            if self.client and self.client.is_connected:
                stopped = self.client.stop()
                if asyncio.iscoroutine(stopped):
                    self.loop.run_until_complete(stopped)
            self.loop.close()

    async def _connect(self) -> None:
        assert self.client is not None
        try:
            authorized = await asyncio.wait_for(self.client.connect(), timeout=60.0)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("انتهت مهلة الاتصال بخوادم Telegram") from exc
        if authorized:
            self._set_state("ready", "")
            self.log("تم فتح جلسة Telegram المحفوظة.")
        else:
            self._set_state("phone", "")
            self.log("الجلسة غير موثقة؛ أدخل رقم الهاتف من الواجهة.")

    def _set_state(self, state: str, error: str = "") -> None:
        with self.lock:
            self.state = state
            self.error = error

    def snapshot(self) -> dict[str, str]:
        with self.lock:
            return {"state": self.state, "error": self.error}

    def submit_phone(self, phone: str) -> None:
        self._submit(self._send_code(phone))

    async def _send_code(self, phone: str) -> None:
        if not self.client:
            return
        if not self.client.is_connected:
            try:
                await asyncio.wait_for(self.client.connect(), timeout=60.0)
            except asyncio.TimeoutError as exc:
                self._set_state("error", "انتهت مهلة الاتصال بخوادم Telegram")
                raise TimeoutError("انتهت مهلة الاتصال بخوادم Telegram") from exc
        sent = await self.client.send_code(phone)
        self.pending_phone = phone
        self.pending_hash = sent.phone_code_hash
        self._set_state("code", "")
        self.log("تم إرسال رمز التحقق إلى تطبيق Telegram.")

    def submit_code(self, code: str) -> None:
        self._submit(self._sign_in(code))

    async def _sign_in(self, code: str) -> None:
        if not self.client:
            return
        try:
            await self.client.sign_in(self.pending_phone, self.pending_hash, code)
            self._set_state("ready", "")
            self.log("تم تسجيل الدخول وحفظ الجلسة.")
        except SessionPasswordNeeded:
            self._set_state("password", "")
            self.log("الحساب يحتاج كلمة مرور التحقق بخطوتين.")

    def submit_password(self, password: str) -> None:
        self._submit(self._check_password(password))

    async def _check_password(self, password: str) -> None:
        if not self.client:
            return
        await self.client.check_password(password)
        self._set_state("ready", "")
        self.log("تم التحقق من كلمة المرور وحفظ الجلسة.")

    def _submit(self, coroutine: Any) -> None:
        if not self.loop or not self.thread or not self.thread.is_alive():
            self._set_state("error", "جلسة Telegram غير جاهزة")
            return
        asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def download(self, link: str, target_root: str, progress: Callable[[int, int], None]) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        future = asyncio.run_coroutine_threadsafe(
            self._download(link, target_root, progress), self.loop
        )
        return future

    async def _download(self, link: str, target_root: str, progress: Callable[[int, int], None]) -> dict[str, Any]:
        if self.state != "ready" or not self.client:
            raise RuntimeError("سجّل الدخول أولًا")
        chat_ref, message_id = parse_message_link(link)
        message = await self.client.get_messages(chat_ref, message_id)
        return await self._download_message(message, target_root, progress)

    async def _download_message(self, message: Any, target_root: str,
                                progress: Callable[[int, int], None]) -> dict[str, Any]:
        if not message or message.empty or not message.media:
            raise ValueError("الرسالة لا تحتوي على وسائط قابلة للتنزيل")
        channel = safe_name(message.chat.title if message.chat else "telegram_channel")
        month = message.date.strftime("%Y_%m") if message.date else "unknown_date"
        folder = Path(target_root).expanduser() / channel / month
        folder.mkdir(parents=True, exist_ok=True)
        context_label = await self._nearby_label(message)
        file_path = folder / message_file_name(message)
        if file_path.exists():
            stem, suffix = file_path.stem, file_path.suffix
            candidate = folder / f"{stem}__msg_{message.id}{suffix}"
            counter = 2
            while candidate.exists():
                candidate = folder / f"{stem}__msg_{message.id}_{counter}{suffix}"
                counter += 1
            file_path = candidate

        # Pyrogram invokes this callback synchronously while chunks arrive.
        # A synchronous callback is important: an async callback would be
        # created but not awaited, leaving the UI stuck at 0%.
        def on_progress(current: int, total: int) -> None:
            progress(current, total)

        result = await self.client.download_media(message, file_name=str(file_path), progress=on_progress)
        if not result:
            raise RuntimeError("Telegram لم يُرجع ملفًا")
        downloaded_path = Path(str(result))
        if not downloaded_path.exists():
            downloaded_path = file_path
        if not downloaded_path.exists():
            candidates = sorted(folder.glob(f"{message.id}_*"), key=lambda item: item.stat().st_mtime, reverse=True)
            if candidates:
                downloaded_path = candidates[0]
        if not downloaded_path.exists():
            raise FileNotFoundError(f"لم يتم العثور على الملف بعد التنزيل: {file_path.name}")
        return {"path": str(downloaded_path), "channel": channel, "message_id": message.id,
                "size": downloaded_path.stat().st_size, "description": context_label}

    async def _nearby_label(self, message: Any) -> str:
        own_text = (getattr(message, "caption", None) or getattr(message, "text", None) or "").strip()
        if own_text:
            return own_text
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        if chat_id is None or not self.client:
            return ""
        context_parts: list[str] = []
        try:
            for offset in range(1, 6):
                next_message = await self.client.get_messages(chat_id, int(message.id) + offset)
                if not next_message or next_message.empty:
                    continue
                next_text = (getattr(next_message, "text", None) or getattr(next_message, "caption", None) or "").strip()
                if next_text:
                    context_parts.append(next_text)
                    break
                for key in ("document", "video", "audio", "voice", "video_note", "photo", "animation"):
                    media = getattr(next_message, key, None)
                    if media is not None:
                        original = getattr(media, "file_name", None)
                        context_parts.append(Path(original).stem if original else key)
                        break
            return " — ".join(context_parts)
        except Exception:
            return " — ".join(context_parts)

    def fetch_message_context(self, message: Any) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        return asyncio.run_coroutine_threadsafe(self._nearby_label(message), self.loop)

    def fetch_message(self, chat_ref: str | int, message_id: int) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        future = asyncio.run_coroutine_threadsafe(
            self.client.get_messages(chat_ref, message_id), self.loop
        )
        return future

    def download_message(self, message: Any, target_root: str,
                         progress: Callable[[int, int], None]) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        return asyncio.run_coroutine_threadsafe(
            self._download_message(message, target_root, progress), self.loop
        )

    def fetch_chat(self, chat_ref: str | int) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        return asyncio.run_coroutine_threadsafe(
            self.client.get_chat(chat_ref), self.loop
        )

    def fetch_chat_member(self, chat_ref: str | int, user_id: int) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        return asyncio.run_coroutine_threadsafe(
            self.client.get_chat_member(chat_ref, user_id), self.loop
        )

    def join_chat(self, chat_ref: str) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        return asyncio.run_coroutine_threadsafe(
            self.client.join_chat(chat_ref), self.loop
        )

    def fetch_messages(self, chat_ref: str | int, message_ids: list[int]) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        return asyncio.run_coroutine_threadsafe(
            self.client.get_messages(chat_ref, message_ids), self.loop
        )

    def fetch_channel_messages(self, chat_ref: str | int, limit: int = 5000) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        return asyncio.run_coroutine_threadsafe(
            self._fetch_channel_messages(chat_ref, limit), self.loop
        )

    async def _fetch_channel_messages(self, chat_ref: str | int, limit: int) -> list[Any]:
        results: list[Any] = []
        async for item in self.client.get_chat_history(chat_ref, limit=0):
            results.append(item)
            if limit and len(results) >= limit:
                break
        return results

    def fetch_messages_by_time(self, chat_ref: str | int, start_time: Any,
                               end_time: Any, limit: int = 1000) -> Any:
        if not self.loop or not self.client:
            raise RuntimeError("جلسة Telegram غير جاهزة")
        return asyncio.run_coroutine_threadsafe(
            self._fetch_messages_by_time(chat_ref, start_time, end_time, limit), self.loop
        )

    async def _fetch_messages_by_time(self, chat_ref: str | int, start_time: Any,
                                      end_time: Any, limit: int) -> list[Any]:
        results: list[Any] = []
        async for item in self.client.get_chat_history(chat_ref, limit=0):
            if item.date and item.date < start_time:
                break
            if item.date and start_time <= item.date <= end_time:
                results.append(item)
                if len(results) >= limit:
                    break
        return results

    def _stop(self) -> None:
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)


def _channel_ref(value: str) -> str | int:
    value = value.strip()
    if value.lstrip("-").isdigit():
        number = int(value)
        return number if value.startswith("-") else int(f"-100{value}")
    return value.lstrip("@").strip()


def _parsed_telegram_url(link: str):
    """Normalize a pasted Telegram URL before parsing it.

    Telegram's share action may produce internal links in either of these
    forms: ``/c/<channel>/<message>`` or
    ``/c/<channel>/<topic>/<message>``.  Users also commonly paste a link
    followed by punctuation or a fragment from the surrounding message.
    """
    value = unquote((link or "").strip().strip("<>\"'"))
    value = value.rstrip(".,،؛:!?؟)]}>")
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    return value, parsed


def _internal_message_parts(parts: list[str]) -> tuple[int, int] | None:
    """Return ``(private chat id, message id)`` for a ``t.me/c`` path.

    The middle numeric segment is a forum topic/thread identifier when
    present; Telegram's actual message identifier is always the final
    numeric segment.  Accepting both two- and three-segment forms avoids
    treating a valid internal message URL as a channel-only URL.
    """
    if len(parts) < 2 or not parts[0].isdigit() or not all(part.isdigit() for part in parts[1:]):
        return None
    return _channel_ref(parts[0]), int(parts[-1])


def _is_internal_chat_path(parts: list[str]) -> bool:
    return bool(parts and parts[0].lower() == "c" and _internal_message_parts(parts[1:]))


def _message_path_parts(parts: list[str]) -> tuple[str | int, int] | None:
    if _is_internal_chat_path(parts):
        result = _internal_message_parts(parts[1:])
        assert result is not None
        return result
    if len(parts) >= 2 and parts[0].lower() not in {"c", "joinchat"} and parts[-1].isdigit():
        return parts[0].lstrip("@"), int(parts[-1])
    return None


def parse_message_link(link: str) -> tuple[str | int, int]:
    value, parsed = _parsed_telegram_url(link)
    if parsed.scheme.lower() == "tg":
        query = parse_qs(parsed.query)
        action = parsed.netloc.lower()
        if action == "resolve":
            chat = query.get("domain", [""])[0]
            post = query.get("post", [""])[0]
            if chat and post.isdigit():
                return _channel_ref(chat), int(post)
        elif action in {"privatepost", "msg_url"}:
            chat = query.get("channel", query.get("chat_id", [""]))[0]
            post = query.get("post", query.get("message_id", [""]))[0]
            if chat and post.isdigit():
                return _channel_ref(chat), int(post)
        elif action == "openmessage":
            chat = query.get("chat_id", query.get("user_id", [""]))[0]
            post = query.get("message_id", [""])[0]
            if chat and post.isdigit():
                return _channel_ref(chat), int(post)
        raise ValueError("رابط رسالة Telegram غير صالح")

    host = parsed.netloc.lower().removesuffix(".")
    if host not in TELEGRAM_HOSTS:
        raise ValueError("رابط Telegram غير صالح")
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0].lower() == "s":
        parts = parts[1:]
    message_ref = _message_path_parts(parts)
    if message_ref is not None:
        # In /c/<channel>/<topic>/<message>, the topic is context only;
        # Telegram fetches the actual post by the final message id.
        return message_ref
    raise ValueError("رابط رسالة Telegram غير صالح")


def parse_chat_link(link: str) -> str | int:
    value, parsed = _parsed_telegram_url(link)
    if parsed.scheme.lower() == "tg":
        query = parse_qs(parsed.query)
        action = parsed.netloc.lower()
        if action == "resolve" and query.get("domain"):
            return _channel_ref(query["domain"][0])
        if action in {"privatepost", "msg_url", "openmessage"}:
            chat = query.get("channel", query.get("chat_id", query.get("user_id", [""])))[0]
            if chat:
                return _channel_ref(chat)
        if action == "join" and query.get("invite"):
            return f"https://t.me/+{query['invite'][0]}"
        raise ValueError("رابط قناة Telegram غير صالح")

    host = parsed.netloc.lower().removesuffix(".")
    if host not in TELEGRAM_HOSTS:
        raise ValueError("رابط القناة غير صالح")
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0].lower() == "s":
        parts = parts[1:]
    if parts and parts[0].lower() == "c" and len(parts) >= 2 and parts[1].isdigit():
        return _channel_ref(parts[1])
    if parts and parts[0].lower() == "joinchat" and len(parts) >= 2:
        return f"https://t.me/joinchat/{parts[1]}"
    if parts and parts[0].startswith("+"):
        return f"https://t.me/{parts[0]}"
    if parts and parts[0] and parts[0].lower() not in {"c", "joinchat"}:
        return parts[0].lstrip("@")
    raise ValueError("رابط القناة غير صالح")


def extract_telegram_links(text: str, limit: int = 20) -> list[str]:
    """Extract and deduplicate Telegram HTTP and tg:// links from free text."""
    # Telegram links are sometimes pasted back-to-back without spaces. Insert a
    # separator at every new Telegram URL start before applying the broad matcher.
    prepared = URL_START_RE.sub(" ", text or "")
    candidates = URL_RE.findall(prepared)
    links: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        link = raw.rstrip(".,،؛:!?؟)]}>")
        try:
            chat_ref, message_id = parse_message_link(link)
            key = f"message:{chat_ref}:{message_id}"
        except ValueError:
            try:
                key = f"chat:{parse_chat_link(link)}"
            except ValueError:
                key = link.lower()
        if key in seen:
            continue
        seen.add(key)
        links.append(link)
        if len(links) >= limit:
            break
    return links


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:120] or "telegram_channel"


def message_file_name(message: Any, context_label: str = "") -> str:
    del context_label  # الوصف السياقي يُحفظ ويُعرض منفصلًا، ولا يغيّر اسم الملف الأصلي.
    for key in ("document", "video", "audio", "voice", "video_note", "photo", "animation"):
        media = getattr(message, key, None)
        if media is not None:
            original = getattr(media, "file_name", None)
            if original:
                return safe_name(Path(str(original)).name)
            fallback_suffixes = {
                "photo": ".jpg", "video": ".mp4", "audio": ".mp3",
                "voice": ".ogg", "video_note": ".mp4", "animation": ".mp4",
                "document": ".bin",
            }
            mime = getattr(media, "mime_type", "") or ""
            suffix = "." + mime.split("/")[-1].replace("jpeg", "jpg") if "/" in mime else fallback_suffixes.get(key, ".bin")
            return safe_name(f"{key}{suffix}")
    return "media.bin"


class DownloadManager:
    def __init__(self, log: Callable[[str], None] | None = None):
        self.log = log or (lambda message: None)
        self.session: TelegramSession | None = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def configure_session(self, api_id: str, api_hash: str, session_path: str) -> None:
        if self.session and self.session.snapshot()["state"] not in ("error", "stopped"):
            return
        self.session = TelegramSession(api_id, api_hash, session_path, self.log)
        self.session.start()

    def session_snapshot(self) -> dict[str, str]:
        return self.session.snapshot() if self.session else {"state": "not_started", "error": ""}

    def submit_auth(self, kind: str, value: str) -> str:
        if not self.session:
            return "لم تبدأ جلسة Telegram بعد"
        if kind == "phone":
            self.session.submit_phone(value)
        elif kind == "code":
            self.session.submit_code(value)
        elif kind == "password":
            self.session.submit_password(value)
        else:
            return "نوع إدخال غير معروف"
        return "تم إرسال الإدخال"

    def submit_download(self, link: str, target_root: str) -> tuple[bool, str]:
        if not URL_RE.match(link.strip()):
            return False, "الرابط يجب أن يكون رابط رسالة Telegram صالحًا"
        if not self.session or self.session.snapshot()["state"] != "ready":
            return False, "سجّل الدخول إلى Telegram أولًا"
        job_id = str(uuid4())
        with self.lock:
            self.jobs[job_id] = {"id": job_id, "status": "queued", "link": link,
                                 "downloaded": 0, "total": 0, "file": "", "error": "",
                                 "started_at": time.time()}
        threading.Thread(target=self._run_job, args=(job_id, link, target_root), daemon=True).start()
        return True, job_id

    def _run_job(self, job_id: str, link: str, target_root: str) -> None:
        self._update(job_id, status="downloading")
        try:
            future = self.session.download(
                link, target_root,
                lambda current, total: self._update(job_id, downloaded=current, total=total),
            )
            result = future.result()
            self._update(job_id, status="completed", file=result["path"], downloaded=result["size"], total=result["size"], result=result)
            self.log(f"اكتمل تنزيل الملف وتنظيمه داخل قناة: {result['channel']}")
        except (RPCError, ValueError, OSError, RuntimeError) as exc:
            self._update(job_id, status="failed", error=str(exc))
            self.log(f"فشل التنزيل: {str(exc)}")
        except Exception as exc:
            self._update(job_id, status="failed", error=type(exc).__name__)
            self.log(f"فشل غير متوقع: {type(exc).__name__}")

    def _update(self, job_id: str, **values: Any) -> None:
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id].update(values)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            jobs = {key: dict(value) for key, value in self.jobs.items()}
        return {"session": self.session_snapshot(), "jobs": jobs}
