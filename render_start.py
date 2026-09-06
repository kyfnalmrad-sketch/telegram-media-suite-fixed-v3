from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

# Pyrogram 2.1.x imports its sync wrapper during module import and expects a
# current event loop. Python 3.14 no longer creates one automatically.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from flask import Flask, jsonify, make_response, request
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

DATA_DIR = Path(os.getenv("TMD_SUITE_DATA", "/opt/render/project/src/.tmd-data"))
SESSION_DIR = DATA_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = SESSION_DIR / "UserBot.session"
app = Flask(__name__)
_bot_process: subprocess.Popen | None = None
_bot_lock = threading.Lock()


def env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


class SessionSetup:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="telegram-session-setup")
        self.client: Client | None = None
        self.phone = ""
        self._phone_code_hash = ""
        self.state = "idle"
        self.error = ""
        self._lock = threading.Lock()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def snapshot(self):
        with self._lock:
            return {
                "state": self.state,
                "error": self.error,
                "phone_set": bool(self.phone),
                "session_file": SESSION_FILE.exists(),
            }

    def _set(self, state: str, error: str = ""):
        with self._lock:
            self.state, self.error = state, error

    def _submit(self, coroutine):
        if not self.thread.is_alive():
            self.thread.start()
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def start_phone(self, phone: str):
        return self._submit(self._start_phone(phone))

    async def _start_phone(self, phone: str):
        old_client = self.client
        try:
            if old_client and old_client.is_connected:
                await old_client.disconnect()
            api_id = int(env_first("TELEGRAM_API_ID", "API_ID"))
            api_hash = env_first("TELEGRAM_API_HASH", "API_HASH")
            if not api_hash:
                raise RuntimeError("أضف TELEGRAM_API_HASH في Render")
            self.phone = phone.strip()
            if not self.phone:
                raise RuntimeError("رقم الهاتف فارغ")
            self.client = Client(
                "UserBot",
                api_id=api_id,
                api_hash=api_hash,
                workdir=str(SESSION_DIR),
                no_updates=True,
            )
            await self.client.connect()
            sent = await self.client.send_code(self.phone)
            self._phone_code_hash = sent.phone_code_hash
            self._set("code")
        except Exception as exc:
            self._set("error", str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)

    def submit_code(self, code: str):
        return self._submit(self._submit_code(code))

    async def _submit_code(self, code: str):
        try:
            if not self.client or not self.client.is_connected:
                raise RuntimeError("ابدأ الجلسة أولًا")
            if not code.strip():
                raise RuntimeError("أدخل كود Telegram")
            await self.client.sign_in(self.phone, self._phone_code_hash, code.strip())
            await self._finish_login()
        except SessionPasswordNeeded:
            self._set("password")
        except Exception as exc:
            self._set("error", str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)

    def submit_password(self, password: str):
        return self._submit(self._submit_password(password))

    async def _submit_password(self, password: str):
        try:
            if not self.client or not self.client.is_connected:
                raise RuntimeError("ابدأ الجلسة أولًا")
            if not password:
                raise RuntimeError("أدخل كلمة مرور التحقق بخطوتين")
            await self.client.check_password(password)
            await self._finish_login()
        except Exception as exc:
            self._set("error", str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)

    async def _finish_login(self):
        # Disconnect cleanly so Pyrogram flushes the authenticated session file.
        if self.client and self.client.is_connected:
            await self.client.disconnect()
        if not SESSION_FILE.exists() or SESSION_FILE.stat().st_size == 0:
            raise RuntimeError("تم الدخول لكن ملف الجلسة لم يُحفظ")
        self._set("ready")

    def close(self):
        if self.client and self.client.is_connected:
            future = self._submit(self.client.disconnect())
            future.result(timeout=15)


setup = SessionSetup()


def authorized() -> bool:
    # The dashboard is intentionally open so Render/browser never shows an
    # HTTP Basic username/password prompt. Telegram verification is handled
    # only by the explicit code and 2FA steps in the page.
    return True


def json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def read_jobs() -> dict:
    path = DATA_DIR / "jobs.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 1, "next_id": 1, "jobs": {}}


def dashboard_login():
    return dashboard()


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "restricted-content-saver", "session": setup.snapshot()})


@app.get("/")
def dashboard():
    state = setup.snapshot()
    response = make_response(
        f"""<!doctype html><meta charset='utf-8'><title>إعداد جلسة Telegram</title>
        <style>body{{font-family:Arial;max-width:760px;margin:40px auto;line-height:1.8;direction:rtl}}input,button{{padding:10px;margin:4px}}button{{cursor:pointer}}#result{{font-weight:bold}}</style>
        <h2>إعداد جلسة Telegram</h2>
        <p>الحالة: <b id='state'>{state['state']}</b></p>
        <p>رقم الهاتف من Render: <span id='phone'>{env_first('TELEGRAM_PHONE_NUMBER', 'PHONE') or 'غير مضبوط'}</span></p>
        <p>التسلسل: <b>بدء جلسة ← الكود ← كلمة مرور التحقق (إن وجدت) ← ترحيل الجلسة ← تشغيل البوت</b></p>
        <button onclick='start()'>بدء جلسة</button><br>
        <div id='codeBox' hidden><input id='code' inputmode='numeric' autocomplete='one-time-code' placeholder='كود Telegram الرقمي' style='width:330px'><button onclick='submitCode()'>تحقق من الكود</button></div>
        <div id='passwordBox' hidden><input id='password' type='password' autocomplete='current-password' placeholder='كلمة مرور التحقق بخطوتين' style='width:330px'><button onclick='submitPassword()'>تحقق من كلمة المرور</button></div>
        <p>إذا ظهرت حالة <b>code</b> أدخل الكود الرقمي الذي أرسله Telegram. إذا ظهرت حالة <b>password</b> أدخل كلمة مرور التحقق بخطوتين، وليس الكود الرقمي.</p>
        <button onclick='transfer()'>ترحيل الجلسة إلى Render</button>
        <button onclick='startBot()'>تشغيل البوت</button>
        <h3>العمليات</h3><div id='jobs'>جارٍ التحميل...</div>
        <p id='result'></p>
        <script>
        async function call(url, body={{}}){{let r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});let x=await r.json();document.getElementById('result').textContent=x.error||x.message||JSON.stringify(x);refresh();}}
        async function start(){{await call('/api/session/start',{{}})}}
        async function submitCode(){{await call('/api/session/code',{{value:document.getElementById('code').value}})}}
        async function submitPassword(){{await call('/api/session/password',{{value:document.getElementById('password').value}})}}
        async function transfer(){{await call('/api/session/transfer',{{}})}}
        async function startBot(){{await call('/api/bot/start',{{}})}}
        async function jobs(){{let r=await fetch('/api/jobs');if(!r.ok)return;let x=await r.json();let rows=Object.values(x.jobs).sort((a,b)=>b.id-a.id).slice(0,20);document.getElementById('jobs').innerHTML=rows.length?rows.map(j=>`<p><b>#${{j.id}}</b> — ${{j.phase||j.status}} — ${{j.progress||0}}%</p>`).join(''): 'لا توجد عمليات';}}
        async function refresh(){{let r=await fetch('/api/session/status');if(!r.ok)return;let x=await r.json();document.getElementById('state').textContent=x.state;document.getElementById('codeBox').hidden=x.state!=='code';document.getElementById('passwordBox').hidden=x.state!=='password';if(x.error)document.getElementById('result').textContent=x.error;}}
        setInterval(refresh,3000); setInterval(jobs,3000); refresh(); jobs();
        </script>"""
    )
    return response


@app.get("/api/jobs")
def jobs_status():
    if not authorized():
        return json_error("غير مصرح", 401)
    return jsonify(read_jobs())


@app.get("/api/session/status")
def session_status():
    if not authorized():
        return json_error("غير مصرح", 401)
    return jsonify(setup.snapshot())


@app.post("/api/session/start")
def session_start():
    if not authorized():
        return json_error("غير مصرح", 401)
    phone = env_first("TELEGRAM_PHONE_NUMBER", "PHONE")
    if not phone:
        return json_error("أضف TELEGRAM_PHONE_NUMBER في Render")
    if not env_first("TELEGRAM_API_ID", "API_ID") or not env_first("TELEGRAM_API_HASH", "API_HASH"):
        return json_error("أضف TELEGRAM_API_ID وTELEGRAM_API_HASH في Render")
    setup.start_phone(phone)
    return jsonify({"ok": True, "message": "تم طلب رمز Telegram", "state": "starting"})


@app.post("/api/session/code")
def session_code():
    if not authorized():
        return json_error("غير مصرح", 401)
    setup.submit_code(str((request.json or {}).get("value", "")))
    return jsonify({"ok": True, "message": "جارٍ التحقق"})


@app.post("/api/session/password")
def session_password():
    if not authorized():
        return json_error("غير مصرح", 401)
    setup.submit_password(str((request.json or {}).get("value", "")))
    return jsonify({"ok": True, "message": "جارٍ التحقق بخطوتين"})


def transfer_session():
    if setup.snapshot()["state"] != "ready":
        raise RuntimeError("أكمل تسجيل الدخول أولًا ثم اضغط ترحيل الجلسة")
    setup.close()
    if not SESSION_FILE.exists() or SESSION_FILE.stat().st_size == 0:
        raise RuntimeError("لا يوجد ملف جلسة صالح؛ أكمل تسجيل الدخول أولًا")
    token = os.getenv("RENDER_API_KEY", "").strip()
    service = os.getenv("RENDER_SERVICE_ID", "").strip()
    if not token or not service:
        raise RuntimeError("أضف RENDER_API_KEY وRENDER_SERVICE_ID في Render")
    encoded = base64.b64encode(SESSION_FILE.read_bytes()).decode("ascii")
    url = f"https://api.render.com/v1/services/{quote(service, safe='')}/env-vars/TMD_SESSION_B64"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = Request(url, data=json.dumps({"value": encoded}).encode(), headers=headers, method="PUT")
    try:
        with urlopen(req, timeout=30):
            pass
    except HTTPError as exc:
        raise RuntimeError(f"Render رفض حفظ الجلسة (HTTP {exc.code})") from exc
    return True


@app.post("/api/session/transfer")
def session_transfer():
    if not authorized():
        return json_error("غير مصرح", 401)
    try:
        transfer_session()
        return jsonify({"ok": True, "message": "تم ترحيل الجلسة إلى TMD_SESSION_B64 في Render. شغّل البوت الآن أو أعد تشغيل الخدمة."})
    except (OSError, URLError, HTTPError, RuntimeError) as exc:
        return json_error(str(exc))


@app.post("/api/bot/start")
def bot_start():
    global _bot_process
    if not authorized():
        return json_error("غير مصرح", 401)
    with _bot_lock:
        if _bot_process is not None and _bot_process.poll() is None:
            return jsonify({"ok": True, "message": "البوت يعمل حاليًا"})
        has_session = bool(env_first("TELEGRAM_SESSION_STRING", "SESSION_STRING")) or bool(env_first("TMD_SESSION_B64")) or SESSION_FILE.exists()
        if not has_session:
            return json_error("أكمل تسجيل جلسة Telegram أو رحّلها أولًا")
        setup.close()
        _bot_process = subprocess.Popen([sys.executable, "-m", "Unlock"], cwd=str(Path(__file__).parent))
    return jsonify({"ok": True, "message": "تم تشغيل البوت"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    if env_first("TELEGRAM_SESSION_STRING", "SESSION_STRING", "TMD_SESSION_B64") or SESSION_FILE.exists():
        _bot_process = subprocess.Popen([sys.executable, "-m", "Unlock"], cwd=str(Path(__file__).parent))
    app.run(host="0.0.0.0", port=port, threaded=True)
