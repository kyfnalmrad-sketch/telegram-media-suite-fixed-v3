from __future__ import annotations

import asyncio
import calendar
import re
import threading
import time
from contextlib import suppress

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Callable
from uuid import uuid4

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from automation import DualAutomationProcessor
from downloader import TelegramSession, extract_telegram_links, parse_chat_link, parse_message_link, safe_name


class TelegramBotService:
    """Bot with guided menus, optional range modes and safe file delivery."""

    def __init__(self, session_getter: Callable[[], TelegramSession | None],
                 storage_getter: Callable[[], str], log: Callable[[str], None],
                 allowlist_saver: Callable[[set[int]], None] | None = None,
                 self_enrollment_enabled: bool = True,
                 session_setup_owner_id: int | None = None):
        self.session_getter = session_getter
        self.storage_getter = storage_getter
        self.log = log
        self.allowlist_saver = allowlist_saver
        self.self_enrollment_enabled = self_enrollment_enabled
        self.session_setup_owner_id = session_setup_owner_id
        self.client: Client | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.bot_token = ""
        self.allowed_user_ids: set[int] = set()
        self.status = "stopped"
        self.error = ""
        self.pending_ranges: dict[str, dict[str, Any]] = {}
        self.user_flows: dict[int, dict[str, Any]] = {}
        self.brand_image = Path(__file__).resolve().parent.parent / "assets" / "wolf_background.png"
        self.pending_ttl_seconds = 24 * 60 * 60
        self.processor = DualAutomationProcessor(workers=2)
        self.enrollment_ttl_seconds = 10 * 60
        self.session_setup_ttl_seconds = 15 * 60
        self.runtime_seconds = 6 * 60 * 60
        self._runtime_timer: threading.Timer | None = None
        self._update_guard_lock = threading.Lock()
        self._seen_updates: dict[tuple[int, int], float] = {}
        self._seen_callback_updates: dict[tuple[int, str], float] = {}
        self._last_start_reply: dict[int, float] = {}
        self._last_access_reply: dict[tuple[int, str], float] = {}
        self._handlers_registered = False

    def start(self, api_id: str, api_hash: str, bot_token: str,
              allowed_user_ids: str) -> tuple[bool, str]:
        if self.thread and self.thread.is_alive():
            return False, "البوت يعمل حاليًا"
        if not api_id.isdigit() or len(api_hash.strip()) != 32 or not bot_token.strip():
            return False, "أدخل api_id وapi_hash وBot Token أولًا"
        allowed = self._parse_ids(allowed_user_ids)
        if self.session_setup_owner_id:
            allowed.add(self.session_setup_owner_id)
        if not allowed and not self.self_enrollment_enabled:
            return False, "أدخل User ID واحدًا على الأقل في قائمة المستخدمين المسموحين"
        session = self.session_getter()
        if (not session or session.snapshot()["state"] != "ready") and not self.session_setup_owner_id and allowed:
            return False, "يجب تسجيل الدخول بالحساب الشخصي أولًا"
        self.bot_token = bot_token.strip()
        self.allowed_user_ids = allowed
        self._handlers_registered = False
        self.status = "starting"
        self.error = ""
        self.thread = threading.Thread(
            target=self._thread_main, args=(int(api_id), api_hash.strip()),
            daemon=True, name="telegram-bot-worker",
        )
        self.thread.start()
        self._runtime_timer = threading.Timer(self.runtime_seconds, self.stop)
        self._runtime_timer.daemon = True
        self._runtime_timer.start()
        return True, "بدأ تشغيل البوت لمدة ست ساعات"

    def _thread_main(self, api_id: int, api_hash: str) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        session = self.session_getter()
        base_workdir = Path(session.session_path if session else str(Path.home())).expanduser()
        workdir = base_workdir.parent / "bot_sessions"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            self.client = Client("tmd_bot", api_id=api_id, api_hash=api_hash,
                                 bot_token=self.bot_token, workdir=str(workdir))
            if not self._handlers_registered:
                self.client.add_handler(self._message_handler(), group=0)
                self.client.add_handler(self._callback_handler(), group=1)
                self._handlers_registered = True
            started = self.client.start()
            if asyncio.iscoroutine(started):
                self.loop.run_until_complete(asyncio.wait_for(started, timeout=60.0))
            self.status = "running"
            self.log("تم تشغيل بوت Telegram بنجاح.")
            self.loop.run_forever()
        except Exception as exc:
            self.status = "error"
            self.error = type(exc).__name__
            self.log(f"فشل تشغيل البوت: {type(exc).__name__}")
        finally:
            if self.client and self.client.is_connected:
                stopped = self.client.stop()
                if asyncio.iscoroutine(stopped):
                    self.loop.run_until_complete(stopped)
            self.loop.close()

    def _authorized(self, message: Any) -> bool:
        return bool(message.from_user and message.from_user.id in self.allowed_user_ids)

    def _is_duplicate_update(self, message: Any) -> bool:
        """Drop a repeated Telegram update before it can create another workflow reply."""
        message_id = getattr(message, "id", None)
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None) or getattr(getattr(message, "from_user", None), "id", None)
        if not isinstance(message_id, int) or chat_id is None:
            return False
        key = (int(chat_id), message_id)
        now = time.monotonic()
        with self._update_guard_lock:
            previous = self._seen_updates.get(key)
            self._seen_updates[key] = now
            if len(self._seen_updates) > 4096:
                cutoff = now - 900
                self._seen_updates = {item: stamp for item, stamp in self._seen_updates.items() if stamp >= cutoff}
        return previous is not None and now - previous < 900

    def _is_duplicate_callback(self, query: Any) -> bool:
        """Drop a retried callback query before it can run the same workflow twice."""
        callback_id = getattr(query, "id", None)
        user = getattr(query, "from_user", None)
        user_id = getattr(user, "id", None)
        if callback_id is None or user_id is None:
            return False
        key = (int(user_id), str(callback_id))
        now = time.monotonic()
        with self._update_guard_lock:
            previous = self._seen_callback_updates.get(key)
            self._seen_callback_updates[key] = now
            if len(self._seen_callback_updates) > 4096:
                cutoff = now - 900
                self._seen_callback_updates = {
                    item: stamp for item, stamp in self._seen_callback_updates.items()
                    if stamp >= cutoff
                }
        return previous is not None and now - previous < 900

    def _reply_allowed(self, user_id: int, kind: str, cooldown: float = 30.0) -> bool:
        """Throttle repeated informational replies caused by retries or unsolicited updates."""
        now = time.monotonic()
        key = (int(user_id), str(kind))
        with self._update_guard_lock:
            previous = self._last_access_reply.get(key, 0.0)
            self._last_access_reply[key] = now
            if len(self._last_access_reply) > 2048:
                cutoff = now - 3600
                self._last_access_reply = {
                    item: stamp for item, stamp in self._last_access_reply.items()
                    if stamp >= cutoff
                }
        return previous <= 0.0 or now - previous >= cooldown

    def _start_reply_allowed(self, user_id: int) -> bool:
        """Throttle rapid repeated /start commands while allowing deliberate later starts."""
        now = time.monotonic()
        with self._update_guard_lock:
            previous = self._last_start_reply.get(user_id, 0.0)
            self._last_start_reply[user_id] = now
            if len(self._last_start_reply) > 1024:
                cutoff = now - 3600
                self._last_start_reply = {item: stamp for item, stamp in self._last_start_reply.items() if stamp >= cutoff}
        return previous <= 0.0 or now - previous >= 60.0

    def _keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("فحص رابط", callback_data="info_help"),
             InlineKeyboardButton("فحص الكاتب", callback_data="author_help")],
            [InlineKeyboardButton("فحص قناة", callback_data="channel_help")],
            [InlineKeyboardButton("تنزيل رابط", callback_data="download_help")],
            [InlineKeyboardButton("نطاق بالرسائل", callback_data="range_messages"),
             InlineKeyboardButton("نطاق بالوقت", callback_data="range_time")],
            [InlineKeyboardButton("حالة البوت", callback_data="status"),
             InlineKeyboardButton("إلغاء", callback_data="cancel")],
        ])

    @staticmethod
    def _error_report(exc: Exception, operation: str) -> str:
        name = type(exc).__name__
        detail = str(exc).strip().lower()
        if isinstance(exc, ValueError) and ("غير موجود" in str(exc) or "not found" in detail):
            cause = "الرسالة أو القناة المطلوبة غير موجودة، أو لم تعد متاحة."
            fix = "انسخ الرابط من Telegram من جديد، وتأكد من أن الرسالة لم تُحذف وأن الحساب يستطيع فتح القناة."
        elif isinstance(exc, ValueError):
            cause = "صيغة الرابط أو البيانات المدخلة غير صحيحة."
            fix = "أرسل رابطًا بصيغة https://t.me/channel أو https://t.me/channel/123، وتأكد من الأرقام المطلوبة."
        elif isinstance(exc, RuntimeError) and ("عضو" in str(exc) or "مشترك" in str(exc)):
            cause = "الحساب المستخدم لا يملك وصولًا إلى المصدر المطلوب."
            fix = "تحقق من الحساب المستخدم للوصول إلى المصدر، ثم أعد المحاولة."
        elif isinstance(exc, RuntimeError) and ("جلسة" in str(exc) or "سجّل" in str(exc)):
            cause = "جلسة الحساب الشخصي غير جاهزة أو انتهت صلاحيتها."
            fix = "افتح الواجهة المحلية، سجّل الدخول بالحساب الشخصي، ثم أعد تشغيل العملية."
        elif name in {"PeerIdInvalid", "ChannelInvalid", "ChatIdInvalid"}:
            cause = "جلسة Telegram لا تملك معلومات الوصول الحالية لهذه القناة (Peer/Access Hash)."
            fix = "افتح القناة من تطبيق Telegram بالحساب الشخصي، ثم أعد إرسال رابط رسالة منها. ويمكنك أيضًا توجيه رسالة منها إلى Saved Messages لتحديث ذاكرة الجلسة."
        elif name in {"UserNotParticipant", "ChannelPrivate", "ChatAdminRequired", "Forbidden"}:
            cause = "الحساب الشخصي المستخدم للجلب ليس عضوًا في القناة أو لا يملك صلاحية الوصول."
            fix = "انضم إلى القناة بالحساب الشخصي، وتأكد أن الرابط من قناة يستطيع هذا الحساب فتحها، ثم أعد المحاولة."
        elif name in {"UsernameInvalid", "UsernameNotOccupied"}:
            cause = "اسم القناة العامة غير موجود أو غير مكتوب بشكل صحيح."
            fix = "تحقق من اسم المستخدم بعد t.me، وأرسل الرابط الكامل دون مسافات."
        elif name in {"MsgIdInvalid", "MessageIdInvalid", "MessageNotModified", "MessageEmpty"}:
            cause = "رقم الرسالة غير صحيح أو لم تعد الرسالة متاحة."
            fix = "افتح الرسالة في Telegram وانسخ رابطها من جديد، وتأكد أنها لم تُحذف."
        elif name in {"FloodWait", "Flood"}:
            cause = "Telegram طلب الانتظار بسبب كثرة الطلبات."
            fix = "انتظر المدة التي يحددها Telegram ثم أعد المحاولة، ولا ترسل عمليات متزامنة كثيرة."
        elif isinstance(exc, FileNotFoundError):
            cause = "تم طلب التنزيل لكن الملف الناتج لم يظهر في مسار التخزين."
            fix = "تحقق من صلاحية مسار التخزين ومساحة القرص، ثم أعد التنزيل. سيبقى الاسم الأصلي محفوظًا عند نجاح العملية."
        elif isinstance(exc, PermissionError):
            cause = "لا توجد صلاحية كتابة في مسار التخزين."
            fix = "اختر مجلدًا يملك التطبيق صلاحية الكتابة فيه، مثل مجلد المستخدم، ثم أعد المحاولة."
        elif isinstance(exc, (TimeoutError, ConnectionError)):
            cause = "انقطع الاتصال أو انتهت مهلة طلب Telegram."
            fix = "تحقق من الإنترنت، انتظر قليلًا، ثم أعد المحاولة دون تغيير الرابط."
        elif name in {"SessionPasswordNeeded", "Unauthorized", "AuthKeyUnregistered"}:
            cause = "جلسة Telegram تحتاج تسجيل دخول أو تحققًا إضافيًا."
            fix = "أعد تسجيل الدخول من الواجهة وأدخل رمز التحقق وكلمة المرور الثنائية عند طلبها."
        elif name == "RPCError":
            cause = "Telegram رفض العملية بسبب خطأ في الوصول أو الطلب."
            fix = "تأكد من الرابط وعضوية الحساب في القناة، ثم أعد المحاولة بعد لحظات."
        else:
            cause = f"حدث خطأ تقني من نوع {name}."
            fix = "جرّب الرابط مرة أخرى، وإذا تكرر الخطأ أرسل نص هذه الرسالة مع سجل العملية دون إرسال أي أسرار."
        return f"تعذر {operation}.\nالسبب: {cause}\nالإصلاح المقترح: {fix}"

    @staticmethod
    def _extract_links(text: str, limit: int = 20) -> list[str]:
        return extract_telegram_links(text, limit=limit)

    def _remember_pending(self, token: str, user_id: int, messages: list[Any]) -> None:
        self.pending_ranges[token] = {
            "user_id": user_id,
            "messages": messages,
            "created_at": time.time(),
            "processing": False,
            "active_ids": set(),
            "delivered_ids": set(),
        }

    def _get_pending(self, token: str, user_id: int) -> dict[str, Any] | None:
        pending = self.pending_ranges.get(token)
        if not pending or pending.get("user_id") != user_id:
            return None
        if time.time() - float(pending.get("created_at", 0)) > self.pending_ttl_seconds:
            self.pending_ranges.pop(token, None)
            return None
        return pending

    @staticmethod
    def _reply_keyboard() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("ابدأ"), KeyboardButton("عودة")],
                [KeyboardButton("فحص رابط"), KeyboardButton("فحص الكاتب")],
                [KeyboardButton("فحص قناة")],
                [KeyboardButton("تنزيل رابط")],
                [KeyboardButton("نطاق بالرسائل"), KeyboardButton("نطاق بالوقت")],
                [KeyboardButton("حالة البوت"), KeyboardButton("القائمة")],
            ],
            resize_keyboard=True,
            is_persistent=True,
            one_time_keyboard=False,
        )

    async def _send_welcome(self, message: Any) -> None:
        welcome = (
            "مرحبًا. اختر العملية من لوحة الأزرار أسفل مربع الكتابة.\n\n"
            "فحص رابط يعرض التفاصيل، وتنزيل رابط يرسل الملف، ويمكنك اختيار النطاق بالرسائل أو بالوقت."
        )
        if self.brand_image.exists():
            try:
                await message.reply_photo(
                    str(self.brand_image),
                    caption=welcome,
                    reply_markup=self._reply_keyboard(),
                )
                return
            except Exception:
                pass
        await message.reply_text(welcome, reply_markup=self._reply_keyboard())

    async def _begin_enrollment(self, message: Any) -> None:
        user_id = message.from_user.id
        self.user_flows[user_id] = {"mode": "enroll_confirm", "created_at": time.time()}
        await message.reply_text(
            f"مرحبًا. User ID الذي استلمه Telegram هو: `{user_id}`\n\n"
            "هل توافق على إضافة هذا الحساب إلى قائمة السماح؟",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("نعم"), KeyboardButton("لا")], [KeyboardButton("عودة")]],
                resize_keyboard=True,
                one_time_keyboard=False,
            ),
        )

    async def _continue_enrollment(self, message: Any, text: str) -> None:
        user_id = message.from_user.id
        flow = self.user_flows.get(user_id) or {}
        if time.time() - float(flow.get("created_at", 0)) > self.enrollment_ttl_seconds:
            self.user_flows.pop(user_id, None)
            await message.reply_text("انتهت صلاحية الموافقة. أرسل /start من جديد.", reply_markup=self._reply_keyboard())
            return
        if text.casefold() in {"نعم", "yes", "أوافق"}:
            self.allowed_user_ids.add(user_id)
            if self.session_setup_owner_id is None:
                session = self.session_getter()
                if not session or session.snapshot().get("state") != "ready":
                    self.session_setup_owner_id = user_id
            try:
                if self.allowlist_saver:
                    self.allowlist_saver(set(self.allowed_user_ids))
            except Exception:
                self.allowed_user_ids.discard(user_id)
                await message.reply_text("تعذر حفظ User ID. راجع الواجهة ثم حاول مرة أخرى.", reply_markup=self._reply_keyboard())
                return
            self.user_flows.pop(user_id, None)
            await message.reply_text("تمت الموافقة وحفظ User ID. أصبح الحساب مصرحًا له.", reply_markup=self._reply_keyboard())
            await self._send_welcome(message)
            return
        if text.casefold() in {"لا", "no", "عودة"}:
            self.user_flows.pop(user_id, None)
            await message.reply_text("لم تتم إضافة الحساب. أرسل /start إذا أردت المحاولة لاحقًا.")
            return
        await message.reply_text("اختر «نعم» للموافقة أو «لا» للرفض.", reply_markup=self._reply_keyboard())

    def _session_setup_prompt(self, state: str) -> str:
        prompts = {
            "starting": "جلسة Telegram قيد الاتصال. انتظر قليلًا ثم أرسل /start مرة أخرى.",
            "phone": "أرسل رقم الهاتف بصيغة دولية. لن أظهره في السجل ولن أرسله لأي مستخدم آخر.",
            "code": "أرسل رمز التحقق الذي أرسله Telegram. لن يتم حفظه في السجل.",
            "password": "أرسل كلمة مرور التحقق بخطوتين. لن يتم حفظها أو عرضها.",
            "error": "تعذر بدء جلسة Telegram. راجع إعدادات API ثم أعد تشغيل الخدمة.",
        }
        return prompts.get(state, "جلسة Telegram غير جاهزة حاليًا. أرسل /start بعد لحظات.")

    def _is_session_owner(self, user_id: int) -> bool:
        return bool(self.session_setup_owner_id and user_id == self.session_setup_owner_id)

    async def _begin_session_setup(self, message: Any) -> None:
        user_id = message.from_user.id
        session = self.session_getter()
        state = session.snapshot()["state"] if session else "error"
        self.user_flows[user_id] = {
            "mode": "session_setup",
            "created_at": time.time(),
        }
        await message.reply_text(
            "وضع إعداد جلسة Telegram للمالك فقط.\n\n"
            + self._session_setup_prompt(state)
            + "\n\nلن يطلب النظام هذه البيانات مرة أخرى بعد نجاح حفظ الجلسة.",
            reply_markup=self._reply_keyboard(),
        )

    async def _continue_session_setup(self, message: Any, text: str) -> None:
        user_id = message.from_user.id
        flow = self.user_flows.get(user_id) or {}
        if time.time() - float(flow.get("created_at", 0)) > self.session_setup_ttl_seconds:
            self.user_flows.pop(user_id, None)
            await message.reply_text("انتهت مهلة إعداد الجلسة. أرسل /start للبدء من جديد.")
            return
        if text.casefold() in {"عودة", "إلغاء", "/cancel", "cancel"}:
            self.user_flows.pop(user_id, None)
            await message.reply_text("تم إلغاء إعداد الجلسة.", reply_markup=self._reply_keyboard())
            return
        session = self.session_getter()
        if not session:
            await message.reply_text("جلسة Telegram غير متاحة حاليًا. أعد تشغيل الخدمة ثم أرسل /start.")
            return
        state = session.snapshot()["state"]
        if state == "ready":
            self.user_flows.pop(user_id, None)
            await message.reply_text("تم تفعيل الجلسة وحفظها. أرسل /start لفتح القائمة.", reply_markup=self._reply_keyboard())
            return
        try:
            if state == "phone":
                session.submit_phone(text)
                response = "تم إرسال رقم الهاتف. انتظر رسالة Telegram ثم أرسل رمز التحقق هنا."
            elif state == "code":
                session.submit_code(text)
                response = "تم إرسال رمز التحقق. إذا طلب Telegram كلمة مرور التحقق بخطوتين فأرسلها هنا."
            elif state == "password":
                session.submit_password(text)
                response = "تم إرسال كلمة المرور. انتظر اكتمال تفعيل الجلسة ثم أرسل /start."
            elif state == "starting":
                response = self._session_setup_prompt(state)
            else:
                response = self._session_setup_prompt(state)
            await message.reply_text(response, reply_markup=self._reply_keyboard())
        except Exception as exc:
            self.log(f"فشل إدخال مرحلة جلسة Telegram: {type(exc).__name__}")
            await message.reply_text("تعذر إرسال البيانات إلى جلسة Telegram. أرسل /start وحاول مجددًا.", reply_markup=self._reply_keyboard())

    def _message_handler(self):
        async def handler(client: Client, message: Any) -> None:
            del client
            if not message.from_user:
                return
            if self._is_duplicate_update(message):
                return
            user_id = message.from_user.id
            text = (message.text or message.caption or "").strip()
            if self._is_session_owner(user_id):
                session = self.session_getter()
                session_state = session.snapshot()["state"] if session else "error"
                if text in ("/start", "/setup", "تفعيل الجلسة") and session_state != "ready":
                    if not self._start_reply_allowed(user_id):
                        return
                    await self._begin_session_setup(message)
                    return
                if (self.user_flows.get(user_id) or {}).get("mode") == "session_setup":
                    await self._continue_session_setup(message, text)
                    return
            if not self._authorized(message):
                if self.self_enrollment_enabled:
                    if text in ("/start", "/help", "/menu"):
                        if not self._start_reply_allowed(user_id):
                            return
                        await self._begin_enrollment(message)
                        return
                    if (self.user_flows.get(user_id) or {}).get("mode") == "enroll_confirm":
                        await self._continue_enrollment(message, text)
                        return
                    if self._reply_allowed(user_id, "unregistered", cooldown=24 * 60 * 60):
                        await message.reply_text("هذا الحساب غير مسجل. أرسل /start لعرض User ID وطلب الموافقة.")
                else:
                    if self._reply_allowed(user_id, "private", cooldown=24 * 60 * 60):
                        await message.reply_text("هذا البوت خاص حاليًا. اطلب من المالك إضافة User ID الخاص بك.")
                return
            if text in ("/start", "/help", "/menu"):
                if not self._start_reply_allowed(user_id):
                    return
                self.user_flows.pop(user_id, None)
                await self._send_welcome(message)
                return
            if text == "/status":
                await message.reply_text(f"حالة البوت: {self.status}", reply_markup=self._reply_keyboard())
                return
            if text in {"ابدأ", "عودة", "فحص رابط", "فحص الكاتب", "فحص قناة", "تنزيل رابط", "نطاق بالرسائل", "نطاق بالوقت", "حالة البوت", "القائمة"}:
                self.user_flows.pop(user_id, None)
                if text in {"ابدأ", "عودة", "القائمة"}:
                    await message.reply_text("اختر العملية من لوحة الأزرار:", reply_markup=self._reply_keyboard())
                elif text == "حالة البوت":
                    await message.reply_text(f"حالة البوت: {self.status}", reply_markup=self._reply_keyboard())
                else:
                    mode_by_label = {
                        "فحص رابط": "info",
                        "فحص الكاتب": "info",
                        "فحص قناة": "channel_link",
                        "تنزيل رابط": "download",
                        "نطاق بالرسائل": "range_link_messages",
                        "نطاق بالوقت": "range_link_time",
                    }
                    mode = mode_by_label[text]
                    self.user_flows[user_id] = {"mode": mode}
                    prompt = {
                        "info": "أرسل الآن رابط الرسالة لفحص تفاصيلها.",
                        "channel_link": "أرسل رابط القناة أو رابط رسالة داخل القناة. سأحدد النوع والقناة تلقائيًا.",
                        "download": "أرسل الآن رابط الرسالة لتنزيلها.",
                        "range_link_messages": "أرسل رابط القناة، ثم سأطلب رقم البداية والنهاية.",
                        "range_link_time": "أرسل رابط القناة، ثم سأطلب وقت البداية والنهاية بتوقيت UTC.",
                    }[mode]
                    await message.reply_text(prompt, reply_markup=self._reply_keyboard())
                return
            flow = self.user_flows.get(user_id)
            if flow:
                await self._continue_flow(message, text, flow)
                return
            if text.startswith("/info "):
                await self._send_info(message, text[6:].strip())
                return
            if text.startswith("/range "):
                await self._send_range(message, text[7:].strip())
                return
            if text.startswith("/timerange "):
                await self._send_time_range(message, text[11:].strip())
                return
            if text.startswith("/download "):
                text = text[10:].strip()
            links = self._extract_links(text)
            if links:
                await self._download_many(message, links)
            else:
                await message.reply_text("أرسل /start لعرض القائمة، أو اختر زرًا من لوحة الأزرار.", reply_markup=self._reply_keyboard())

        return MessageHandler(handler, filters=filters.private)

    async def _continue_flow(self, message: Any, text: str, flow: dict[str, Any]) -> None:
        user_id = message.from_user.id
        mode = flow.get("mode")
        if mode in {"info", "download"}:
            links = self._extract_links(text)
            if not links:
                await message.reply_text("السبب: لم أجد رابط Telegram قابلًا للتحليل.\nالإصلاح: أرسل رابطًا مثل t.me/channel/123 أو https://t.me/c/123/456، ويمكن إرسال عدة روابط.", reply_markup=self._reply_keyboard())
                return
            self.user_flows.pop(user_id, None)
            if mode == "info":
                await self._send_info_many(message, links)
            else:
                await self._download_many(message, links)
            return
        if mode == "channel_link":
            links = self._extract_links(text)
            if not links:
                await message.reply_text("السبب: لم أجد رابط قناة أو رسالة Telegram قابلًا للتحليل.\nالإصلاح: أرسل t.me/channel أو t.me/channel/123، ويمكن أيضًا إرسال رابط دعوة.", reply_markup=self._reply_keyboard())
                return
            text = links[0]
            try:
                normalized = text if "://" in text else "https://" + text
                parsed_input = urlparse(normalized)
                path_parts = [part for part in parsed_input.path.split("/") if part]
                if path_parts and path_parts[0] == "s":
                    path_parts = path_parts[1:]
                is_invite = bool(path_parts and (path_parts[0].startswith("+") or path_parts[0] == "joinchat"))
                is_message_link = not is_invite and len(path_parts) >= 2 and path_parts[-1].isdigit()
                session = self.session_getter()
                if not session:
                    raise RuntimeError("جلسة الحساب غير متاحة")
                source_message = None
                invite_link = None
                if path_parts and (path_parts[0].startswith("+") or path_parts[0] == "joinchat"):
                    invite_link = normalized.split("?", 1)[0].rstrip("/")
                    chat_ref = invite_link
                    chat = SimpleNamespace(id=invite_link, title="قناة عبر رابط دعوة", username=None, type=SimpleNamespace(value="invite"))
                elif is_message_link:
                    parsed_ref, message_id = parse_message_link(text)
                    source_message = await asyncio.wrap_future(session.fetch_message(parsed_ref, message_id))
                    chat = source_message.chat if source_message else None
                    chat_ref = getattr(chat, "id", None) or parsed_ref
                else:
                    chat_ref = parse_chat_link(text)
                    chat = await asyncio.wrap_future(session.fetch_chat(chat_ref))
                if not chat:
                    raise ValueError("تعذر الوصول إلى القناة")
                flow["chat_ref"] = chat_ref
                flow["chat"] = chat
                flow["from_message_link"] = is_message_link
                flow["invite_link"] = invite_link
                flow["accessible_without_membership"] = True
                flow["mode"] = "channel_membership"
                await message.reply_text(
                    "هل أنت مشترك في هذه القناة؟ أجب بـ «نعم» أو «لا».\n\n"
                    "هذا السؤال للتأكد من أن الحساب الشخصي يستطيع قراءة القناة الخاصة. إذا كانت القناة عامة فسيحاول النظام التحقق من الوصول مباشرة.",
                    reply_markup=ReplyKeyboardMarkup(
                        [[KeyboardButton("نعم"), KeyboardButton("لا")], [KeyboardButton("عودة")]],
                        resize_keyboard=True,
                        one_time_keyboard=False,
                    ),
                )
            except Exception as exc:
                await message.reply_text(self._error_report(exc, "تحليل رابط القناة"), reply_markup=self._reply_keyboard())
            return
        if mode == "channel_membership":
            answer = text.casefold()
            if answer not in {"نعم", "لا", "yes", "no"}:
                await message.reply_text("أجب بـ «نعم» أو «لا» حتى أتحقق من الوصول إلى القناة.", reply_markup=self._reply_keyboard())
                return
            if answer in {"لا", "no"}:
                if flow.get("accessible_without_membership"):
                    flow["mode"] = "channel_choice"
                    await message.reply_text(
                        "تم الوصول إلى بيانات القناة العامة دون اشتراك. اختر طريقة الفحص الآن.",
                        reply_markup=self._reply_keyboard(),
                    )
                    await self._send_channel_choice(message, flow["chat"], flow.get("from_message_link", False))
                    return
                flow["mode"] = "channel_join_confirm"
                await message.reply_text(
                    "تعذر جلب البيانات دون اشتراك. هل تريد أن يحاول الحساب الشخصي الانضمام إلى القناة تلقائيًا؟\n"
                    "أرسل «نعم انضم» للمحاولة، أو «عودة» للإلغاء. لن يتم تجاوز طلبات الانضمام أو صلاحيات القنوات الخاصة.",
                    reply_markup=ReplyKeyboardMarkup(
                        [[KeyboardButton("نعم انضم"), KeyboardButton("عودة")], [KeyboardButton("ابدأ")]],
                        resize_keyboard=True,
                        one_time_keyboard=False,
                    ),
                )
                return
            await self._verify_channel_membership(message, flow)
            return
        if mode == "channel_join_confirm":
            if text not in {"نعم انضم", "انضم", "yes join"}:
                await message.reply_text("أرسل «نعم انضم» للمحاولة، أو «عودة» لإلغاء العملية.", reply_markup=self._reply_keyboard())
                return
            await self._join_and_continue(message, flow)
            return
        if mode == "channel_start":
            if not text.isdigit():
                await message.reply_text("أرسل رقم الرسالة الأولى فقط.", reply_markup=self._reply_keyboard())
                return
            flow["start"] = int(text)
            flow["mode"] = "channel_end"
            await message.reply_text("أرسل رقم الرسالة الأخيرة، بحد أقصى ١٠٠ رسالة.", reply_markup=self._reply_keyboard())
            return
        if mode == "channel_end":
            if not text.isdigit():
                await message.reply_text("أرسل رقم نهاية صحيحًا. استخدم ٠ مع بداية ١ لفحص كل الرسائل.", reply_markup=self._reply_keyboard())
                return
            end_value = int(text)
            start_value = int(flow["start"])
            if start_value == 1 and end_value == 0:
                await self._begin_date_scan(message, flow["chat_ref"])
                return
            if end_value < start_value:
                await message.reply_text("أرسل رقم نهاية أكبر من أو يساوي البداية، أو استخدم ٠ مع بداية ١ لفحص الكل.", reply_markup=self._reply_keyboard())
                return
            if end_value - start_value > 100:
                await message.reply_text("الحد الأقصى للنطاق المحدد هو ١٠٠ رسالة. لفحص الكل استخدم البداية ١ والنهاية ٠.", reply_markup=self._reply_keyboard())
                return
            self.user_flows.pop(user_id, None)
            await self._scan_range(message, flow["chat_ref"], start_value, end_value)
            return
        if mode == "channel_time_start":
            try:
                flow["start_time"] = parse_utc_datetime(text)
                flow["mode"] = "channel_time_end"
                await message.reply_text("أرسل وقت النهاية بصيغة `YYYY-MM-DD HH:MM` بتوقيت UTC.", reply_markup=self._reply_keyboard())
            except ValueError:
                await message.reply_text("صيغة الوقت غير صحيحة. استخدم `YYYY-MM-DD HH:MM`.", reply_markup=self._reply_keyboard())
            return
        if mode == "channel_time_end":
            try:
                end_time = parse_utc_datetime(text)
                if end_time <= flow["start_time"]:
                    raise ValueError
                self.user_flows.pop(user_id, None)
                await self._scan_time_range(message, flow["chat_ref"], flow["start_time"], end_time)
            except ValueError:
                await message.reply_text("وقت النهاية يجب أن يكون بعد البداية وبالصيغة الصحيحة.", reply_markup=self._reply_keyboard())
            return
        if mode in {"range_link_messages", "range_link_time"}:
            links = self._extract_links(text)
            if not links:
                await message.reply_text("السبب: لم أجد رابط قناة Telegram.\nالإصلاح: أرسل t.me/channel أو https://t.me/c/1234567890.", reply_markup=self._reply_keyboard())
                return
            try:
                flow["chat_ref"] = parse_chat_link(links[0])
                flow["mode"] = "range_start" if mode == "range_link_messages" else "time_start"
                if mode == "range_link_messages":
                    await message.reply_text("أرسل رقم الرسالة الأولى، مثل: `1`")
                else:
                    await message.reply_text("أرسل وقت البداية بصيغة `YYYY-MM-DD HH:MM` بتوقيت UTC.")
            except Exception as exc:
                await message.reply_text(self._error_report(exc, "قراءة رابط القناة"), reply_markup=self._reply_keyboard())
            return
        if mode == "range_start":
            if not text.isdigit():
                await message.reply_text("أرسل رقم البداية فقط.")
                return
            flow["start"] = int(text)
            flow["mode"] = "range_end"
            await message.reply_text("أرسل رقم الرسالة الأخيرة.")
            return
        if mode == "range_end":
            if not text.isdigit():
                await message.reply_text("أرسل رقم نهاية صحيحًا. استخدم ٠ مع بداية ١ لفحص كل الرسائل.", reply_markup=self._reply_keyboard())
                return
            end_value = int(text)
            start_value = int(flow["start"])
            if start_value == 1 and end_value == 0:
                await self._begin_date_scan(message, flow["chat_ref"])
                return
            if end_value < start_value:
                await message.reply_text("أرسل رقم نهاية أكبر من أو يساوي البداية، أو استخدم ٠ مع بداية ١ لفحص الكل.", reply_markup=self._reply_keyboard())
                return
            if end_value - start_value > 100:
                await message.reply_text("الحد الأقصى للنطاق المحدد هو ١٠٠ رسالة. لفحص الكل استخدم البداية ١ والنهاية ٠.", reply_markup=self._reply_keyboard())
                return
            self.user_flows.pop(user_id, None)
            await self._scan_range(message, flow["chat_ref"], start_value, end_value)
            return
        if mode == "time_start":
            try:
                flow["start_time"] = parse_utc_datetime(text)
                flow["mode"] = "time_end"
                await message.reply_text("أرسل وقت النهاية بالصيغة نفسها `YYYY-MM-DD HH:MM`.")
            except ValueError:
                await message.reply_text("صيغة الوقت غير صحيحة. استخدم `YYYY-MM-DD HH:MM`.")
            return
        if mode == "time_end":
            try:
                end_time = parse_utc_datetime(text)
                if end_time <= flow["start_time"]:
                    raise ValueError
                self.user_flows.pop(user_id, None)
                await self._scan_time_range(message, flow["chat_ref"], flow["start_time"], end_time)
            except ValueError:
                await message.reply_text("وقت النهاية يجب أن يكون بعد البداية وبالصيغة الصحيحة.")

    def _callback_handler(self):
        async def callback(client: Client, query: Any) -> None:
            del client
            if not query.from_user or query.from_user.id not in self.allowed_user_ids:
                await query.answer("غير مصرح", show_alert=True)
                return
            if self._is_duplicate_callback(query):
                return
            user_id = query.from_user.id
            data = query.data or ""
            await query.answer()
            if data == "status":
                await query.message.reply_text(f"حالة البوت: {self.status}")
            elif data in {"info_help", "author_help"}:
                self.user_flows[user_id] = {"mode": "info"}
                await query.message.reply_text("أرسل الآن رابط الرسالة لفحص تفاصيلها والكاتب الظاهر فيها.")
            elif data == "download_help":
                self.user_flows[user_id] = {"mode": "download"}
                await query.message.reply_text("أرسل الآن رابط الرسالة لتنزيلها.")
            elif data == "channel_help":
                self.user_flows[user_id] = {"mode": "channel_link"}
                await query.message.reply_text("أرسل رابط القناة أو رابط رسالة داخل القناة. سأحدد النوع والقناة تلقائيًا.")
            elif data == "range_messages":
                self.user_flows[user_id] = {"mode": "range_link_messages"}
                await query.message.reply_text("أرسل رابط القناة، ثم سأطلب رقم البداية والنهاية.")
            elif data == "range_time":
                self.user_flows[user_id] = {"mode": "range_link_time"}
                await query.message.reply_text("أرسل رابط القناة، ثم سأطلب وقت البداية والنهاية بتوقيت UTC.")
            elif data == "channel_full":
                flow = self.user_flows.get(user_id)
                if flow and flow.get("chat_ref") is not None:
                    flow["mode"] = "channel_full_year"
                    await self._show_date_picker(query.message, flow, "year")
                else:
                    await query.message.reply_text("انتهت جلسة فحص القناة، أرسل «فحص قناة» من القائمة مجددًا.", reply_markup=self._reply_keyboard())
            elif data == "channel_messages":
                flow = self.user_flows.get(user_id)
                if flow and flow.get("mode") == "channel_choice":
                    flow["mode"] = "channel_start"
                    await query.message.reply_text("أرسل رقم الرسالة الأولى لفحص القناة، مثل: `1`", reply_markup=self._reply_keyboard())
                else:
                    await query.message.reply_text("انتهت جلسة فحص القناة.", reply_markup=self._reply_keyboard())
            elif data == "channel_time":
                flow = self.user_flows.get(user_id)
                if flow and flow.get("mode") == "channel_choice":
                    flow["mode"] = "channel_time_start"
                    await query.message.reply_text("أرسل وقت البداية بصيغة `YYYY-MM-DD HH:MM` بتوقيت UTC.", reply_markup=self._reply_keyboard())
                else:
                    await query.message.reply_text("انتهت جلسة فحص القناة.", reply_markup=self._reply_keyboard())
            elif data.startswith("scan_date_year:"):
                flow = self.user_flows.get(user_id)
                if flow and flow.get("mode") == "channel_full_year" and data.split(":", 1)[1].isdigit():
                    flow["year"] = int(data.split(":", 1)[1])
                    flow["mode"] = "channel_full_month"
                    await self._show_date_picker(query.message, flow, "month")
            elif data.startswith("scan_date_month:"):
                flow = self.user_flows.get(user_id)
                if flow and flow.get("mode") == "channel_full_month" and data.split(":", 1)[1].isdigit():
                    month = int(data.split(":", 1)[1])
                    if 1 <= month <= 12:
                        flow["month"] = month
                        flow["mode"] = "channel_full_day"
                        await self._show_date_picker(query.message, flow, "day")
            elif data.startswith("scan_date_day:"):
                flow = self.user_flows.get(user_id)
                if flow and flow.get("mode") == "channel_full_day" and data.split(":", 1)[1].isdigit():
                    day = int(data.split(":", 1)[1])
                    year, month = int(flow.get("year", 0)), int(flow.get("month", 0))
                    if 1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1]:
                        start_time = datetime(year, month, day, tzinfo=timezone.utc)
                        if start_time > datetime.now(timezone.utc):
                            await query.message.reply_text("لا يمكن اختيار تاريخ مستقبلي.", reply_markup=self._reply_keyboard())
                        else:
                            self.user_flows.pop(user_id, None)
                            await self._scan_channel_from_date(query.message, flow["chat_ref"], start_time)
            elif data.startswith("scan_date_back:"):
                flow = self.user_flows.get(user_id)
                target = data.split(":", 1)[1]
                if flow and target in {"year", "month"}:
                    flow["mode"] = "channel_full_year" if target == "year" else "channel_full_month"
                    await self._show_date_picker(query.message, flow, target)
            elif data in {"cancel", "menu", "start"}:
                self.user_flows.pop(user_id, None)
                await query.message.reply_text("اختر العملية:", reply_markup=self._reply_keyboard())
            elif data.startswith("range_all:"):
                await self._download_pending_range(query.message, data.split(":", 1)[1])
            elif data.startswith("range_one:"):
                parts = data.split(":")
                if len(parts) == 3 and parts[2].isdigit():
                    await self._download_pending_one(query.message, parts[1], int(parts[2]))

        return CallbackQueryHandler(callback)

    async def _send_info_many(self, message: Any, links: list[str]) -> None:
        if len(links) == 1:
            await self._send_info(message, links[0])
            return
        session = self.session_getter()
        if not session:
            await message.reply_text(self._error_report(RuntimeError("جلسة الحساب غير متاحة"), "فحص الروابط"), reply_markup=self._reply_keyboard())
            return
        media_items: list[Any] = []
        reports: list[str] = []
        for index, link in enumerate(links, 1):
            try:
                chat_ref, message_id = parse_message_link(link)
                item = await asyncio.wrap_future(session.fetch_message(chat_ref, message_id))
                if not item or item.empty:
                    raise ValueError("الرسالة غير موجودة")
                chat = item.chat
                caption = (item.caption or item.text or "بدون نص").strip()
                context_description = ""
                if item.media:
                    context_description = await asyncio.wrap_future(session.fetch_message_context(item))
                    media_items.append(item)
                author = self._author_info(item)
                author_text = "غير متاح"
                if author:
                    author_text = (
                        f"الاسم: {author['name']} | النوع: {author['kind']} | "
                        f"User ID: {author['id'] or 'غير متاح'} | "
                        f"Username: @{author['username']}" if author['username'] else
                        f"الاسم: {author['name']} | النوع: {author['kind']} | User ID: {author['id'] or 'غير متاح'}"
                    )
                reports.append(
                    f"{index}) الرابط: {link}\n"
                    f"القناة: {safe_name(chat.title if chat else str(chat_ref))}\n"
                    f"رقم الرسالة: {item.id}\n"
                    f"الكاتب: {author_text}\n"
                    f"النوع: {item.media or 'لا يوجد'}\n"
                    f"الحجم: {self._message_size(item)} bytes\n"
                    f"الوصف: {caption[:500]}\n"
                    f"السياق التالي: {(context_description or 'غير متاح')[:500]}"
                )
            except Exception as exc:
                reports.append(f"{index}) الرابط: {link}\n{self._error_report(exc, 'فحص هذا الرابط')}")
        markup = self._keyboard()
        if media_items:
            token = uuid4().hex[:10]
            self._remember_pending(token, message.from_user.id, media_items)
            rows = [
                [InlineKeyboardButton("تنزيل كل الملفات الناجحة", callback_data=f"range_all:{token}")],
                [InlineKeyboardButton("عودة", callback_data="menu")],
            ]
            for item in media_items[:12]:
                rows.append([InlineKeyboardButton(f"تنزيل الرسالة {item.id}", callback_data=f"range_one:{token}:{item.id}")])
            markup = InlineKeyboardMarkup(rows)
        text = "تم فحص الروابط المتعددة:\n\n" + "\n\n".join(reports)
        if len(text) > 3900:
            text = text[:3800] + "\n\nتم اختصار التقرير؛ استخدم كل رابط منفردًا للتفاصيل الكاملة."
        await message.reply_text(text, reply_markup=markup)

    async def _send_info(self, message: Any, link: str) -> None:
        try:
            chat_ref, message_id = parse_message_link(link)
            session = self.session_getter()
            if not session:
                raise RuntimeError("جلسة الحساب غير متاحة")
            item = await asyncio.wrap_future(session.fetch_message(chat_ref, message_id))
            if not item or item.empty:
                raise ValueError("الرسالة غير موجودة")
            chat = item.chat
            caption = item.caption or item.text or ""
            context_description = ""
            if item.media:
                context_description = await asyncio.wrap_future(session.fetch_message_context(item))
            author = self._author_info(item)
            author_lines = "بيانات الكاتب: مخفية من Telegram؛ لا يمكن استخراج هوية الكاتب من هذا الرابط."
            author_button = None
            if author:
                author_lines = (
                    f"بيانات الكاتب: {author['name']} ({author['kind']})\n"
                    f"User ID: {author['id'] or 'غير متاح'}\n"
                    f"Username: @{author['username']}" if author['username'] else
                    f"بيانات الكاتب: {author['name']} ({author['kind']})\n"
                    f"User ID: {author['id'] or 'غير متاح'}\nUsername: غير متاح"
                )
                if author.get("url"):
                    button_label = "فتح محادثة الكاتب" if author.get("kind") == "مستخدم" else "فتح حساب الكاتب"
                    author_button = InlineKeyboardButton(button_label, url=author["url"])
            markup = self._keyboard()
            buttons: list[list[InlineKeyboardButton]] = []
            if item.media:
                token = uuid4().hex[:10]
                self._remember_pending(token, message.from_user.id, [item])
                buttons.append([InlineKeyboardButton("تنزيل هذا الملف", callback_data=f"range_one:{token}:{item.id}")])
            if author_button:
                buttons.append([author_button])
            elif not author:
                buttons.append([InlineKeyboardButton("فتح الرسالة الأصلية", url=link)])
            if buttons:
                buttons.extend([
                    [InlineKeyboardButton("ابدأ", callback_data="start")],
                    [InlineKeyboardButton("عودة", callback_data="menu")],
                ])
                markup = InlineKeyboardMarkup(buttons)
            await message.reply_text(
                f"القناة: {safe_name(chat.title if chat else str(chat_ref))}\n"
                f"معرّف الرسالة: {item.id}\n"
                f"التاريخ: {item.date or 'غير متاح'}\n"
                f"{author_lines}\n"
                f"نوع المحتوى: {item.media or 'لا يوجد'}\n"
                f"الحجم: {self._message_size(item)} bytes\n"
                f"الوصف: {(caption or 'بدون نص')[:1000]}\n"
                f"الوصف/السياق التالي: {(context_description or 'غير متاح')[:1000]}",
                reply_markup=markup,
            )
        except Exception as exc:
            await message.reply_text(self._error_report(exc, "فحص الرابط"), reply_markup=self._reply_keyboard())

    @staticmethod
    def _mention(message: Any) -> str:
        user = getattr(message, "from_user", None)
        if not user:
            return ""
        name = (getattr(user, "first_name", None) or "يا مستخدم").replace("[", "(").replace("]", ")")
        return f"[{name}](tg://user?id={user.id})"

    async def _join_and_continue(self, message: Any, flow: dict[str, Any]) -> None:
        try:
            session = self.session_getter()
            if not session:
                raise RuntimeError("جلسة الحساب غير متاحة")
            chat_ref = flow["chat_ref"]
            if not isinstance(chat_ref, str):
                raise RuntimeError("لا يمكن تنفيذ انضمام تلقائي من رابط داخلي خاص")
            chat = await asyncio.wrap_future(session.join_chat(chat_ref))
            flow["chat_ref"] = getattr(chat, "id", None) or chat_ref
            flow["chat"] = chat
            flow["mode"] = "channel_choice"
            await message.reply_text("تمت محاولة الانضمام بنجاح، وسأتابع الفحص الآن.", reply_markup=self._reply_keyboard())
            await self._send_channel_choice(message, chat, flow.get("from_message_link", False))
        except Exception as exc:
            await message.reply_text(self._error_report(exc, "الانضمام التلقائي إلى القناة"), reply_markup=self._reply_keyboard())

    async def _verify_channel_membership(self, message: Any, flow: dict[str, Any]) -> None:
        try:
            session = self.session_getter()
            if not session:
                raise RuntimeError("جلسة الحساب غير متاحة")
            member = await asyncio.wrap_future(session.fetch_chat_member(flow["chat_ref"], message.from_user.id))
            status = getattr(getattr(member, "status", None), "value", None) or str(getattr(member, "status", ""))
            if status.casefold() in {"left", "kicked", "banned"}:
                raise RuntimeError("الحساب ليس عضوًا في القناة")
            flow["mode"] = "channel_choice"
            await self._send_channel_choice(message, flow["chat"], flow.get("from_message_link", False))
        except Exception as exc:
            await message.reply_text(self._error_report(exc, "التحقق من الاشتراك في القناة"), reply_markup=self._reply_keyboard())

    async def _send_channel_choice(self, message: Any, chat: Any, from_message_link: bool) -> None:
        title = safe_name(getattr(chat, "title", None) or getattr(chat, "first_name", None) or "القناة")
        username = getattr(chat, "username", None)
        chat_type = getattr(getattr(chat, "type", None), "value", None) or str(getattr(chat, "type", "غير معروف"))
        source_kind = "رابط رسالة داخل القناة" if from_message_link else "رابط قناة"
        await message.reply_text(
            f"تم تحليل {source_kind}.\n"
            f"القناة: {title}\n"
                            f"النوع: {chat_type}\n"
                f"المعرّف: {getattr(chat, 'id', 'غير متاح')}\n"
                f"المعرّف العام: @{username if username else 'غير متاح (قد تكون خاصة)'}\n\n"
                "اختر طريقة الفحص. ويمكن استخدام بداية ١ ونهاية ٠ لفحص كل الرسائل المتاحة.",

            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("فحص القناة كاملًا", callback_data="channel_full")],
                [InlineKeyboardButton("تحديد نطاق رسائل", callback_data="channel_messages")],
                [InlineKeyboardButton("تحديد فترة زمنية", callback_data="channel_time")],
                [InlineKeyboardButton("ابدأ", callback_data="start"), InlineKeyboardButton("عودة", callback_data="menu")],
            ]),
        )

    async def _begin_date_scan(self, message: Any, chat_ref: str | int) -> None:
        flow = {"mode": "channel_full_year", "chat_ref": chat_ref}
        self.user_flows[message.from_user.id] = flow
        await self._show_date_picker(message, flow, "year")

    @staticmethod
    def _ordered_unique_media(messages: list[Any] | None) -> list[Any]:
        """Remove repeated message objects and return media in oldest-to-newest order."""
        unique: list[Any] = []
        seen_ids: set[int] = set()
        for item in messages or []:
            if not item or getattr(item, "empty", False) or not getattr(item, "media", None):
                continue
            message_id = getattr(item, "id", None)
            if isinstance(message_id, int):
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
            unique.append(item)
        unique.sort(key=lambda item: (
            getattr(item, "date", None) or datetime.min.replace(tzinfo=timezone.utc),
            int(getattr(item, "id", 0) or 0),
        ))
        return unique

    @staticmethod
    def _arabic_digits(value: int) -> str:
        return str(value).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))

    async def _show_date_picker(self, message: Any, flow: dict[str, Any], step: str) -> None:
        now = datetime.now(timezone.utc)
        if step == "year":
            years = range(now.year, max(2000, now.year - 12), -1)
            rows = [[InlineKeyboardButton(self._arabic_digits(year), callback_data=f"scan_date_year:{year}")]
                    for year in years]
            text = "اختر سنة بداية الفحص:"
        elif step == "month":
            months = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
                      "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
            rows = [
                [InlineKeyboardButton(months[index], callback_data=f"scan_date_month:{index + 1}")
                 for index in range(start, min(start + 3, 12))]
                for start in range(0, 12, 3)
            ]
            rows.append([InlineKeyboardButton("عودة للسنة", callback_data="scan_date_back:year")])
            text = f"اختر شهر البداية من سنة {self._arabic_digits(int(flow['year']))}:"
        else:
            year, month = int(flow["year"]), int(flow["month"])
            last_day = calendar.monthrange(year, month)[1]
            rows = [
                [InlineKeyboardButton(self._arabic_digits(day), callback_data=f"scan_date_day:{day}")
                 for day in range(start, min(start + 5, last_day + 1))]
                for start in range(1, last_day + 1, 5)
            ]
            rows.append([InlineKeyboardButton("عودة للشهر", callback_data="scan_date_back:month")])
            text = f"اختر يوم البداية من {self._arabic_digits(year)}-{self._arabic_digits(month)}:"
        rows.append([InlineKeyboardButton("إلغاء", callback_data="cancel")])
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))

    @staticmethod
    def _media_file_name(message: Any) -> str:
        for key in ("document", "video", "audio", "voice", "video_note", "photo", "animation"):
            media = getattr(message, key, None)
            if media is not None:
                name = getattr(media, "file_name", None)
                if name:
                    return Path(str(name)).name
                return key
        return "غير متاح"

    def _channel_item_report(self, item: Any, index: int) -> str:
        author = self._author_info(item)
        author_text = "غير متاح"
        if author:
            author_text = author["name"]
            if author.get("id"):
                author_text += f" | ID: {author['id']}"
            if author.get("username"):
                author_text += f" | @{author['username']}"
        date = getattr(item, "date", None)
        date_text = date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if date else "غير متاح"
        description = (getattr(item, "caption", None) or getattr(item, "text", None) or "بدون وصف").strip()
        media_kind = getattr(item, "media", None) or "وسيط"
        return (
            f"{index}. الرسالة {getattr(item, 'id', 'غير متاح')} | {date_text}\n"
            f"النوع: {media_kind}\n"
            f"اسم الملف الأصلي: {self._media_file_name(item)}\n"
            f"الكاتب: {author_text}\n"
            f"الوصف: {description[:500]}"
        )

    async def _scan_channel_from_date(self, message: Any, chat_ref: str | int, start_time: datetime) -> None:
        try:
            session = self.session_getter()
            if not session:
                raise RuntimeError("جلسة الحساب غير متاحة")
            mention = self._mention(message)
            await message.reply_text(
                f"{mention} بدأ فحص القناة من {start_time.strftime('%Y-%m-%d')} بترتيب زمني من أول رسالة في اليوم...",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self._reply_keyboard(),
            )
            messages = await asyncio.wrap_future(
                session.fetch_messages_by_time(chat_ref, start_time, datetime.now(timezone.utc), 0)
            )
            await self._offer_range(message, messages)
        except Exception as exc:
            await message.reply_text(
                f"{self._mention(message)}\n{self._error_report(exc, 'فحص القناة من التاريخ المحدد')}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self._reply_keyboard(),
            )

    async def _scan_channel_full(self, message: Any, chat_ref: str | int) -> None:
        try:
            session = self.session_getter()
            if not session:
                raise RuntimeError("جلسة الحساب غير متاحة")
            mention = self._mention(message)
            progress_message = await message.reply_text(
                f"{mention} بدأ فحص القناة كاملًا. سأجمع الرسائل والوسائط الآن...",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self._reply_keyboard(),
            )
            messages = await asyncio.wrap_future(session.fetch_channel_messages(chat_ref, 0))
            media_messages = self._ordered_unique_media(messages)
            try:
                await progress_message.edit_text(
                    f"{mention} اكتمل الفحص الأولي: عثرت على {len(messages or [])} رسالة، منها {len(media_messages)} وسيط.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            await self._offer_range(message, media_messages)
        except Exception as exc:
            await message.reply_text(f"{self._mention(message)}\n{self._error_report(exc, 'فحص القناة')}", parse_mode=ParseMode.MARKDOWN, reply_markup=self._reply_keyboard())

    async def _scan_range(self, message: Any, chat_ref: str | int, start: int, end: int) -> None:
        try:
            session = self.session_getter()
            if not session:
                raise RuntimeError("جلسة الحساب غير متاحة")
            await message.reply_text(f"{self._mention(message)} جارٍ فحص الرسائل من {start} إلى {end}...", parse_mode=ParseMode.MARKDOWN, reply_markup=self._reply_keyboard())
            messages = await asyncio.wrap_future(session.fetch_messages(chat_ref, list(range(start, end + 1))))
            await self._offer_range(message, messages)
        except Exception as exc:
            await message.reply_text(self._error_report(exc, "فحص نطاق الرسائل"), reply_markup=self._reply_keyboard())

    async def _scan_time_range(self, message: Any, chat_ref: str | int,
                               start_time: datetime, end_time: datetime) -> None:
        try:
            session = self.session_getter()
            if not session:
                raise RuntimeError("جلسة الحساب غير متاحة")
            await message.reply_text(f"{self._mention(message)} جارٍ فحص الرسائل داخل الفترة الزمنية بتوقيت UTC...", parse_mode=ParseMode.MARKDOWN, reply_markup=self._reply_keyboard())
            messages = await asyncio.wrap_future(session.fetch_messages_by_time(chat_ref, start_time, end_time, 1000))
            await self._offer_range(message, messages)
        except Exception as exc:
            await message.reply_text(self._error_report(exc, "فحص النطاق الزمني"), reply_markup=self._reply_keyboard())

    async def _offer_range(self, message: Any, media_messages: list[Any]) -> None:
        media_messages = self._ordered_unique_media(media_messages)
        if not media_messages:
            await message.reply_text("لم أجد وسائط في النطاق.", reply_markup=self._keyboard())
            return
        token = uuid4().hex[:10]
        self._remember_pending(token, message.from_user.id, media_messages)
        rows = [
            [InlineKeyboardButton("تنزيل كل الوسائط", callback_data=f"range_all:{token}")],
            [InlineKeyboardButton("عودة", callback_data="menu")],
        ]

        for item in media_messages[:12]:
            rows.append([InlineKeyboardButton(f"تنزيل الرسالة {item.id}", callback_data=f"range_one:{token}:{item.id}")])
        summary_items = [self._channel_item_report(item, index) for index, item in enumerate(media_messages[:20], 1)]
        summary = "\n\n".join(summary_items)
        if len(summary) > 3600:
            summary = summary[:3500] + "\n\nتم اختصار العرض؛ زر التنزيل الجماعي يحتفظ بكل العناصر الفريدة."
        await message.reply_text(
            f"تم الفحص بالترتيب من الأقدم إلى الأحدث. عدد الوسائط الفريدة: {len(media_messages)}\n\n{summary}",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def _send_range(self, message: Any, value: str) -> None:
        try:
            parts = value.split()
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                start, end = int(parts[1]), int(parts[2])
                chat_ref = parse_chat_link(parts[0])
                if start == 1 and end == 0:
                    await self._begin_date_scan(message, chat_ref)
                    return
                if end >= start and end - start <= 100:
                    await self._scan_range(message, chat_ref, start, end)
                    return
            raise ValueError("صيغة النطاق غير صحيحة؛ استخدم ١ ٠ لفحص كل الرسائل")
        except Exception as exc:
            await message.reply_text(self._error_report(exc, "قراءة نطاق الرسائل"), reply_markup=self._reply_keyboard())

    async def _send_time_range(self, message: Any, value: str) -> None:
        try:
            parts = value.split("|")
            if len(parts) == 3:
                start = parse_utc_datetime(parts[1].strip())
                end = parse_utc_datetime(parts[2].strip())
                if end > start:
                    await self._scan_time_range(message, parse_chat_link(parts[0].strip()), start, end)
                    return
            raise ValueError("صيغة النطاق الزمني غير صحيحة")
        except Exception as exc:
            await message.reply_text(self._error_report(exc, "قراءة النطاق الزمني"), reply_markup=self._reply_keyboard())

    async def _download_pending_one(self, message: Any, token: str, message_id: int) -> None:
        pending = self._get_pending(token, message.from_user.id)
        if not pending:
            await message.reply_text("انتهت صلاحية الاختيار أو لم يعد متاحًا. أرسل «ابدأ» لفحص جديد.", reply_markup=self._reply_keyboard())
            return
        for item in pending["messages"]:
            if item.id == message_id:
                delivered = pending.setdefault("delivered_ids", set())
                active = pending.setdefault("active_ids", set())
                if message_id in delivered or message_id in active:
                    await message.reply_text("هذا العنصر قيد التنزيل أو أُرسل بالفعل ضمن الاختيار الحالي.", reply_markup=self._reply_keyboard())
                    return
                active.add(message_id)
                try:
                    await self._send_downloaded(message, item)
                    delivered.add(message_id)
                finally:
                    active.discard(message_id)
                return
        await message.reply_text("الرسالة غير موجودة في الاختيار الحالي.")

    async def _download_pending_range(self, message: Any, token: str) -> None:
        pending = self._get_pending(token, message.from_user.id)
        if not pending:
            await message.reply_text("انتهت صلاحية الاختيار أو لم يعد متاحًا. أرسل «ابدأ» لفحص جديد.", reply_markup=self._reply_keyboard())
            return
        if pending.get("processing"):
            await message.reply_text("التنزيل الجماعي قيد التنفيذ بالفعل؛ لن أكرر الملفات.", reply_markup=self._reply_keyboard())
            return
        pending["processing"] = True
        items = [item for item in self._ordered_unique_media(list(pending["messages"]))
                 if getattr(item, "id", None) not in pending.setdefault("delivered_ids", set())]
        if not items:
            await message.reply_text("تم تنزيل عناصر هذا الاختيار أو لا توجد عناصر جديدة.", reply_markup=self._reply_keyboard())
            pending["processing"] = False
            return
        await message.reply_text(f"بدأ المعالجان تنزيل {len(items)} ملفًا فريدًا...", reply_markup=self._reply_keyboard())

        async def worker(item: Any, worker_id: int) -> bool:
            await self._send_downloaded(message, item, worker_id)
            return True

        results = await self.processor.run(items, worker)
        delivered = pending.setdefault("delivered_ids", set())
        for result in results:
            if result.error is None and getattr(result.job, "id", None) is not None:
                delivered.add(result.job.id)
        pending["processing"] = False
        success = sum(1 for result in results if result.error is None)
        failed = len(results) - success
        for result in results:
            if result.error is not None:
                await message.reply_text(
                    f"المعالج {result.worker_id} — الرسالة {getattr(result.job, 'id', '?')}:\n"
                    + self._error_report(result.error, "تنزيل ملف من الاختيار"),
                    reply_markup=self._reply_keyboard(),
                )
        self.pending_ranges.pop(token, None)
        await message.reply_text(f"اكتمل التنزيل الجماعي بالمعالجين. النجاح: {success}، الفشل: {failed}.", reply_markup=self._reply_keyboard())

    @staticmethod
    def _unique_links(links: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for link in links:
            value = link.strip()
            normalized = value.split("#", 1)[0].split("?", 1)[0].rstrip("/").casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(value)
        return unique

    async def _download_many(self, message: Any, links: list[str]) -> None:
        links = self._unique_links(links)
        if len(links) == 1:
            await self._download_one(message, links[0], worker_id=1)
            return
        await message.reply_text(f"{self._mention(message)} بدأ المعالجان تنزيل {len(links)} روابط معًا، مع عزل نتيجة كل رابط.", parse_mode=ParseMode.MARKDOWN, reply_markup=self._reply_keyboard())

        async def worker(link: str, worker_id: int) -> bool:
            return await self._download_one(message, link, worker_id=worker_id)

        results = await self.processor.run(links, worker)
        success = sum(1 for result in results if result.error is None and result.value)
        failed = len(results) - success
        for result in results:
            if result.error is not None:
                await message.reply_text(
                    f"المعالج {result.worker_id} — الرابط {result.index + 1}:\n"
                    + self._error_report(result.error, "تنفيذ رابط التنزيل"),
                    reply_markup=self._reply_keyboard(),
                )
        await message.reply_text(
            f"اكتملت معالجة الروابط بالمعالجين. النجاح: {success}، الفشل: {failed}.",
            reply_markup=self._reply_keyboard(),
        )

    async def _download_one(self, message: Any, link: str, worker_id: int = 1) -> bool:
        try:
            chat_ref, message_id = parse_message_link(link)
            session = self.session_getter()
            if not session:
                raise RuntimeError("جلسة الحساب غير متاحة")
            item = await asyncio.wrap_future(session.fetch_message(chat_ref, message_id))
            await self._send_downloaded(message, item, worker_id)
        except Exception as exc:
            self.log(f"فشل طلب البوت: {type(exc).__name__}")
            await message.reply_text(self._error_report(exc, "تنفيذ رابط التنزيل"), reply_markup=self._reply_keyboard())
            return False
        return True

    async def _send_downloaded(self, message: Any, item: Any, worker_id: int = 1) -> None:
        if not item or item.empty or not item.media:
            await message.reply_text("الرسالة لا تحتوي على ملف وسائط.", reply_markup=self._reply_keyboard())
            return
        session = self.session_getter()
        if not session:
            raise RuntimeError("جلسة الحساب غير جاهزة")
        progress_message = await message.reply_text(
            f"المعالج {worker_id}: بدء التنزيل...\n[░░░░░░░░░░░░] ٠٪",
            reply_markup=self._reply_keyboard(),
        )
        progress_state: dict[str, Any] = {
            "current": 0,
            "total": 0,
            "started_at": time.monotonic(),
            "done": False,
        }

        def progress(current: int, total: int) -> None:
            # This callback runs in the downloader thread; it only updates shared state.
            progress_state["current"] = max(0, int(current or 0))
            progress_state["total"] = max(0, int(total or 0))

        pulse_task = asyncio.create_task(self._progress_pulse(progress_message, progress_state))
        try:
            result = await asyncio.wrap_future(session.download_message(item, self.storage_getter(), progress))
        finally:
            progress_state["done"] = True
            pulse_task.cancel()
            with suppress(asyncio.CancelledError):
                await pulse_task
        path = Path(result["path"])
        if not path.exists():
            raise FileNotFoundError(path.name)
        size_mb = result["size"] / (1024 * 1024)
        description = (result.get("description") or "").strip()
        caption = (
            "اكتمل التنزيل والتنظيم\n"
            f"القناة: {safe_name(result['channel'])}\n"
            f"رقم الرسالة: {result['message_id']}\n"
            f"اسم الملف: {path.name}\n"
            f"الحجم: {size_mb:.2f} MB"
        )
        if description:
            caption += f"\nالوصف/السياق التالي: {description[:1200]}"
        try:
            await progress_message.edit_text(
                "اكتمل التنزيل ١٠٠٪\n" + path.name,
                reply_markup=self._reply_keyboard(),
            )
        except Exception:
            pass
        await message.reply_document(str(path), caption=caption, reply_markup=self._reply_keyboard())
        self.log(f"أرسل البوت ملفًا من قناة: {result['channel']}")

    @staticmethod
    def _progress_metrics(current: int, total: int, elapsed: float) -> tuple[float, float, float | None]:
        safe_elapsed = max(float(elapsed), 0.001)
        safe_total = max(int(total), 0)
        safe_current = max(0, min(int(current), safe_total)) if safe_total else max(0, int(current))
        percent = (safe_current * 100.0 / safe_total) if safe_total else 0.0
        speed_mbps = safe_current / safe_elapsed / (1024 * 1024)
        eta = ((safe_total - safe_current) / (speed_mbps * 1024 * 1024)) if speed_mbps > 0 and safe_total else None
        return percent, speed_mbps, eta

    async def _progress_pulse(self, progress_message: Any, progress_state: dict[str, Any]) -> None:
        """Refresh the Telegram progress message while a download is active."""
        while not progress_state.get("done"):
            total = int(progress_state.get("total", 0))
            current = int(progress_state.get("current", 0))
            elapsed = time.monotonic() - float(progress_state.get("started_at", time.monotonic()))
            if total > 0:
                await self._edit_progress(progress_message, current, total, elapsed)
            else:
                # Keep the UI visibly alive until Telegram reports the size.
                await self._edit_progress_indeterminate(progress_message, elapsed)
            await asyncio.sleep(1.0)

    async def _edit_progress_indeterminate(self, progress_message: Any, elapsed: float) -> None:
        frames = ("▰▱▱▱▱▱▱▱▱▱▱▱", "▱▰▱▱▱▱▱▱▱▱▱▱", "▱▱▰▱▱▱▱▱▱▱▱▱", "▱▱▱▰▱▱▱▱▱▱▱▱")
        frame = frames[int(max(0.0, elapsed)) % len(frames)]
        try:
            await progress_message.edit_text(
                f"جارٍ تجهيز التنزيل\n[{frame}]\nالبيانات: في انتظار حجم الملف…",
                reply_markup=self._reply_keyboard(),
            )
        except Exception:
            pass

    async def _edit_progress(self, progress_message: Any, current: int, total: int, elapsed: float = 0.0) -> None:
        if total <= 0:
            return
        percent, speed_mbps, eta = self._progress_metrics(current, total, elapsed or 0.001)
        filled = min(12, int(percent // 8.34))
        bar = "■" * filled + "░" * (12 - filled)
        # A moving marker makes the UI feel alive even when the byte counter
        # pauses briefly between chunks; the percentage remains authoritative.
        if percent < 100.0 and filled < 12:
            frames = ("▸", "▹", "▸", "▹")
            bar = bar[:filled] + frames[int(time.monotonic()) % len(frames)] + bar[filled + 1:]
        current_mb = current / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        eta_text = "—" if eta is None else f"{int(max(0, eta))} ث"
        try:
            await progress_message.edit_text(
                f"جارٍ التنزيل\n[{bar}] {percent:.1f}%\n{current_mb:.2f} / {total_mb:.2f} MB\n"
                f"السرعة: {speed_mbps:.2f} MB/s — المتبقي: {eta_text}",
                reply_markup=self._reply_keyboard(),
            )
        except Exception:
            pass

    @staticmethod
    def _author_info(message: Any) -> dict[str, Any] | None:
        user = getattr(message, "from_user", None)
        sender_chat = getattr(message, "sender_chat", None)
        author = user or sender_chat
        if not author:
            return None
        user_id = getattr(author, "id", None)
        first_name = (getattr(author, "first_name", None) or "").strip()
        last_name = (getattr(author, "last_name", None) or "").strip()
        title = (getattr(author, "title", None) or "").strip()
        username = (getattr(author, "username", None) or "").strip().lstrip("@")
        display_name = " ".join(part for part in (first_name, last_name) if part) or title or "غير متاح"
        profile_url = f"https://t.me/{username}" if username else (f"tg://user?id={user_id}" if user_id else None)
        return {
            "name": display_name,
            "id": user_id,
            "username": username,
            "url": profile_url,
            "kind": "مستخدم" if user else "حساب/قناة مرسلة",
        }

    @staticmethod
    def _message_size(message: Any) -> int:
        for key in ("document", "video", "audio", "voice", "video_note", "photo", "animation"):
            media = getattr(message, key, None)
            if media is not None:
                return int(getattr(media, "file_size", 0) or 0)
        return 0

    def stop(self) -> None:
        timer = self._runtime_timer
        self._runtime_timer = None
        if timer and timer is not threading.current_thread():
            timer.cancel()
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.status = "stopped"

    @staticmethod
    def _parse_ids(value: str) -> set[int]:
        result: set[int] = set()
        for item in value.split(","):
            item = item.strip()
            if item and item.lstrip("-").isdigit():
                result.add(int(item))
        return result


def parse_utc_datetime(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
