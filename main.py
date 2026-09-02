#!/usr/bin/env python3
"""مدخل التشغيل الأساسي لجلسة Telegram واحدة وبوت Telegram.

يؤدي الملف المهام التالية:

١. يقرأ API_ID وAPI_HASH من متغيرات البيئة أو يطلبهما محليًا.
٢. يتحقق من جلسة tmd_user الحالية.
٣. إذا لم تكن الجلسة موثقة، ينفذ تسجيل الدخول مرة واحدة من الطرفية، دون
   تسجيل رمز التحقق أو كلمة مرور التحقق بخطوتين.
٤. يحفظ Pyrogram الجلسة في مجلد sessions ويعيد استخدامها في التشغيلات اللاحقة.
٥. يشغل TelegramBotService بعد جاهزية جلسة الحساب الشخصي.

لا يكتب هذا الملف أي سر إلى Git أو السجل. في Render يجب تمرير القيم عبر
Environment Variables/Secrets، بينما تحفظ الجلسة في TMD_SESSION_B64 وفق
آلية scripts/render_start.py الحالية.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from pyrogram import Client  # noqa: E402
from pyrogram.errors import SessionPasswordNeeded  # noqa: E402

from bot_service import TelegramBotService  # noqa: E402
from downloader import TelegramSession  # noqa: E402


SESSION_NAME = os.environ.get("TMD_SESSION_NAME", "tmd_user").strip() or "tmd_user"
DATA_DIR = Path(
    os.environ.get("TMD_SUITE_DATA", str(ROOT / ".tmd-data"))
).expanduser()
SESSION_DIR = Path(
    os.environ.get(
        "TMD_SESSION_PATH",
        os.environ.get("SESSION_PATH", str(DATA_DIR / "sessions")),
    )
).expanduser()
STORAGE_DIR = Path(
    os.environ.get(
        "TMD_STORAGE_PATH",
        os.environ.get("STORAGE_PATH", str(DATA_DIR / "storage")),
    )
).expanduser()


class MainError(RuntimeError):
    """خطأ قابل للعرض للمستخدم دون تضمين بيانات اعتماد."""


def log(message: str) -> None:
    """اكتب رسالة تشغيل لا تحتوي على أسرار."""
    print(f"[telegram-media-suite] {message}", flush=True)


def _interactive() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _value_from_env_or_prompt(
    env_name: str,
    prompt: str,
    *,
    secret: bool = False,
    required: bool = True,
) -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    if _interactive():
        value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
    if required and not value:
        raise MainError(f"المتغير {env_name} غير مضبوط ولا يمكن طلبه في هذا التشغيل")
    return value


def _api_id() -> int:
    raw = _value_from_env_or_prompt(
        "API_ID", "أدخل API ID: ", required=True
    )
    if not raw.isdigit() or int(raw) <= 0:
        raise MainError("API_ID يجب أن يكون رقمًا صحيحًا موجبًا")
    return int(raw)


def _api_hash() -> str:
    value = _value_from_env_or_prompt(
        "API_HASH", "أدخل API HASH: ", secret=True, required=True
    )
    if len(value) != 32:
        raise MainError("API_HASH يجب أن يتكون من ٣٢ محرفًا")
    return value


def _allowed_ids() -> str:
    value = os.environ.get("ALLOWED_USER_IDS", "").strip()
    if not value and _interactive():
        value = input("أدخل User IDs المسموح لهم مفصولة بفواصل: ").strip()
    valid: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if item and item.lstrip("-").isdigit():
            valid.append(item)
    if not valid:
        raise MainError("ALLOWED_USER_IDS يجب أن يحتوي User ID واحدًا على الأقل")
    return ",".join(dict.fromkeys(valid))


def _prepare_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        SESSION_DIR.chmod(0o700)
        STORAGE_DIR.chmod(0o700)
    except OSError:
        pass


def _session_file() -> Path:
    return SESSION_DIR / f"{SESSION_NAME}.session"


def _chmod_session() -> None:
    path = _session_file()
    if path.exists():
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _prompt_phone() -> str:
    value = os.environ.get("PHONE", "").strip()
    if not value and _interactive():
        value = input("أدخل رقم الهاتف بصيغة دولية: ").strip()
    if not value:
        raise MainError("PHONE مطلوب لأول تسجيل دخول للجلسة")
    return value


def _prompt_code() -> str:
    if not _interactive():
        raise MainError(
            "الجلسة غير موثقة وتحتاج رمز Telegram؛ شغّل main.py من طرفية تفاعلية"
        )
    return getpass.getpass("أدخل رمز التحقق الذي أرسله Telegram: ").strip()


def _prompt_password() -> str:
    if not _interactive():
        raise MainError(
            "الحساب يحتاج كلمة مرور التحقق بخطوتين؛ شغّل main.py من طرفية تفاعلية"
        )
    return getpass.getpass("أدخل كلمة مرور التحقق بخطوتين: ").strip()


async def _login_or_reuse(api_id: int, api_hash: str) -> None:
    """تحقق من الجلسة الحالية أو أنشئها مرة واحدة ثم أغلق عميل الإعداد."""
    client = Client(
        SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(SESSION_DIR),
        no_updates=True,
    )
    connected = False
    try:
        authorized = await client.connect()
        connected = True
        if authorized:
            log("الجلسة المحفوظة موثقة؛ سيعاد استخدامها دون طلب رمز جديد.")
            return

        phone = _prompt_phone()
        sent_code = await client.send_code(phone)
        code = _prompt_code()
        try:
            await client.sign_in(phone, sent_code.phone_code_hash, code)
        except SessionPasswordNeeded:
            password = _prompt_password()
            await client.check_password(password)
        log("تم تسجيل الدخول وحفظ جلسة Telegram بنجاح.")
    except MainError:
        raise
    except Exception as exc:
        # لا نطبع نص الاستثناء، إذ قد يحتوي بعض بيانات الاتصال أو الحساب.
        raise MainError(f"تعذر إكمال تسجيل جلسة Telegram: {type(exc).__name__}") from exc
    finally:
        if connected:
            try:
                await client.disconnect()
            except Exception:
                pass
        _chmod_session()


def _wait_for_ready(session: TelegramSession, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = session.snapshot()
        if state["state"] == "ready":
            return
        if state["state"] == "error":
            raise MainError("تعذر تشغيل جلسة Telegram؛ راجع اتصال Render وإعداداته")
        time.sleep(0.25)
    raise MainError("انتهت مهلة انتظار جاهزية جلسة Telegram")


def _bot_token() -> str:
    return _value_from_env_or_prompt(
        "BOT_TOKEN", "أدخل Bot Token: ", secret=True, required=True
    )


def _shutdown(bot: TelegramBotService | None, session: TelegramSession | None) -> None:
    if bot is not None:
        try:
            bot.stop()
        except Exception:
            pass
    if session is not None and session.loop and session.loop.is_running():
        session.loop.call_soon_threadsafe(session.loop.stop)
    log("تم إيقاف الخدمات بأمان.")


def main() -> int:
    bot: TelegramBotService | None = None
    session: TelegramSession | None = None
    try:
        _prepare_directories()
        api_id = _api_id()
        api_hash = _api_hash()
        log("جارٍ التحقق من جلسة Telegram الوحيدة...")
        asyncio.run(_login_or_reuse(api_id, api_hash))

        session = TelegramSession(
            str(api_id), str(api_hash), str(SESSION_DIR), log
        )
        session.start()
        _wait_for_ready(session)
        log("جلسة Telegram جاهزة.")

        if os.environ.get("TMD_LOGIN_ONLY", "").strip().lower() in {
            "1", "true", "yes", "on"
        }:
            log("اكتمل إعداد الجلسة فقط؛ لم يتم تشغيل البوت بسبب TMD_LOGIN_ONLY.")
            return 0

        token = _bot_token()
        allowed_ids = _allowed_ids()
        bot = TelegramBotService(
            session_getter=lambda: session,
            storage_getter=lambda: str(STORAGE_DIR),
            log=log,
            self_enrollment_enabled=False,
        )
        ok, message = bot.start(str(api_id), api_hash, token, allowed_ids)
        if not ok:
            raise MainError(message)
        log("البوت يعمل باستخدام جلسة الحساب المحفوظة.")

        stop_event = asyncio.Event()

        def request_stop(*_: Any) -> None:
            if not stop_event.is_set():
                stop_event.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(signum, request_stop)
            except (OSError, ValueError):
                pass

        while not stop_event.is_set():
            if bot.status == "error":
                raise MainError("توقف البوت بسبب خطأ في تشغيل عميل Telegram")
            time.sleep(1.0)
        return 0
    except KeyboardInterrupt:
        return 0
    except MainError as exc:
        log(str(exc))
        return 1
    finally:
        _shutdown(bot, session)


if __name__ == "__main__":
    raise SystemExit(main())
