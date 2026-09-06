from __future__ import annotations

import base64
import asyncio
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

from flask import Flask, jsonify, make_response, request
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

DATA_DIR = Path(os.getenv("TMD_SUITE_DATA", "/opt/render/project/src/.tmd-data"))
SESSION_DIR = DATA_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = SESSION_DIR / "UserBot.session"
DASHBOARD_TOKEN = (os.getenv("TMD_DASHBOARD_TOKEN") or os.getenv("ADMIN_SECRET_KEY") or "").strip()

app = Flask(__name__)
_bot_process: subprocess.Popen | None = None


class SessionSetup:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.client: Client | None = None
        self.phone = ""
        self.state = "idle"
        self.error = ""
        self._lock = threading.Lock()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def snapshot(self):
        with self._lock:
            return {"state": self.state, "error": self.error, "phone_set": bool(self.phone)}

    def _set(self, state, error=""):
        with self._lock:
            self.state, self.error = state, error

    def start_phone(self, phone: str):
        if not self.thread.is_alive():
            self.thread.start()
        return asyncio.run_coroutine_threadsafe(self._start_phone(phone), self.loop)

    async def _start_phone(self, phone: str):
        try:
            api_id = int(os.getenv("API_ID") or os.getenv("TELEGRAM_API_ID"))
            api_hash = os.getenv("API_HASH") or os.getenv("TELEGRAM_API_HASH")
            self.phone = phone.strip()
            self.client = Client("UserBot", api_id=api_id, api_hash=api_hash, workdir=str(SESSION_DIR), no_updates=True)
            await self.client.connect()
            sent = await self.client.send_code(self.phone)
            self._phone_code_hash = sent.phone_code_hash
            self._set("code")
        except Exception as exc:
            self._set("error", type(exc).__name__)

    def submit_code(self, code: str):
        return asyncio.run_coroutine_threadsafe(self._submit_code(code), self.loop)

    async def _submit_code(self, code: str):
        try:
            await self.client.sign_in(self.phone, self._phone_code_hash, code.strip())
            self._set("ready")
        except SessionPasswordNeeded:
            self._set("password")
        except Exception as exc:
            self._set("error", type(exc).__name__)

    def submit_password(self, password: str):
        return asyncio.run_coroutine_threadsafe(self._submit_password(password), self.loop)

    async def _submit_password(self, password: str):
        try:
            await self.client.check_password(password)
            self._set("ready")
        except Exception as exc:
            self._set("error", type(exc).__name__)


setup = SessionSetup()


def authorized() -> bool:
    if not DASHBOARD_TOKEN:
        return not os.getenv("RENDER")
    supplied = request.headers.get("X-Dashboard-Token", "") or request.cookies.get("dashboard_token", "") or request.args.get("token", "")
    return bool(supplied) and hmac.compare_digest(supplied, DASHBOARD_TOKEN)


def json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "restricted-content-saver", "session": setup.snapshot()})


@app.get("/")
def dashboard():
    if not authorized():
        return json_error("أدخل TMD_DASHBOARD_TOKEN في ترويسة X-Dashboard-Token", 401)
    state = setup.snapshot()
    response = make_response(f"""<!doctype html><meta charset='utf-8'><title>إعداد الجلسة</title>
    <style>body{{font-family:Arial;max-width:700px;margin:40px auto;line-height:1.8}}input,button{{padding:10px;margin:4px}}button{{cursor:pointer}}</style>
    <h2>إعداد جلسة Telegram</h2><p>الحالة: <b id='state'>{state['state']}</b></p>
    <p>رقم الهاتف من Render: <span id='phone'>{os.getenv('PHONE') or os.getenv('TELEGRAM_PHONE_NUMBER') or 'غير مضبوط'}</span></p>
    <button onclick='start()'>بدء جلسة</button><br>
    <input id='value' placeholder='الكود أو كلمة مرور التحقق' style='width:330px'><button onclick='submitValue()'>إرسال</button>
    <button onclick='transfer()'>ترحيل الجلسة إلى Render</button><button onclick='startBot()'>تشغيل البوت</button><p id='result'></p>
    <script>
    async function call(url, body={{}}){{let r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});let x=await r.json();document.getElementById('result').textContent=x.error||x.message||JSON.stringify(x); refresh();}}
    async function start(){{await call('/api/session/start',{{}})}}
    async function submitValue(){{let v=document.getElementById('value').value;let s=document.getElementById('state').textContent;await call(s==='password'?'/api/session/password':'/api/session/code',{{value:v}})}}
    async function transfer(){{await call('/api/session/transfer',{{}})}}
    async function startBot(){{await call('/api/bot/start',{{}})}}
    async function refresh(){{let x=await (await fetch('/api/session/status')).json();document.getElementById('state').textContent=x.state;}}
    setInterval(refresh,3000);
    </script>""")
    token = request.args.get("token", "").strip()
    if token and DASHBOARD_TOKEN and hmac.compare_digest(token, DASHBOARD_TOKEN):
        response.set_cookie("dashboard_token", token, secure=True, httponly=True, samesite="Lax")
    return response


@app.get("/api/session/status")
def session_status():
    if not authorized(): return json_error("غير مصرح", 401)
    return jsonify(setup.snapshot())


@app.post("/api/session/start")
def session_start():
    if not authorized(): return json_error("غير مصرح", 401)
    phone = os.getenv("PHONE") or os.getenv("TELEGRAM_PHONE_NUMBER")
    if not phone: return json_error("أضف PHONE أو TELEGRAM_PHONE_NUMBER في Render")
    setup.start_phone(phone)
    return jsonify({"ok": True, "message": "تم طلب رمز Telegram", "state": "starting"})


@app.post("/api/session/code")
def session_code():
    if not authorized(): return json_error("غير مصرح", 401)
    setup.submit_code(str((request.json or {}).get("value", "")))
    return jsonify({"ok": True, "message": "جارٍ التحقق"})


@app.post("/api/session/password")
def session_password():
    if not authorized(): return json_error("غير مصرح", 401)
    setup.submit_password(str((request.json or {}).get("value", "")))
    return jsonify({"ok": True, "message": "جارٍ التحقق بخطوتين"})


def transfer_session():
    if not SESSION_FILE.exists():
        raise RuntimeError("لا يوجد ملف جلسة؛ أكمل تسجيل الدخول أولًا")
    token = os.getenv("RENDER_API_KEY", "").strip()
    service = os.getenv("RENDER_SERVICE_ID", "").strip()
    if not token or not service:
        raise RuntimeError("أضف RENDER_API_KEY وRENDER_SERVICE_ID في Render")
    encoded = base64.b64encode(SESSION_FILE.read_bytes()).decode("ascii")
    url = f"https://api.render.com/v1/services/{quote(service, safe='')}/env-vars/TMD_SESSION_B64"
    req = Request(url, data=json.dumps({"value": encoded}).encode(), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="PUT")
    try:
        with urlopen(req, timeout=30):
            pass
    except HTTPError as exc:
        if exc.code != 404:
            raise RuntimeError(f"Render رفض حفظ الجلسة (HTTP {exc.code})") from exc
        create = Request(f"https://api.render.com/v1/services/{quote(service, safe='')}/env-vars", data=json.dumps({"key": "TMD_SESSION_B64", "value": encoded}).encode(), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
        with urlopen(create, timeout=30):
            pass
    return True


@app.post("/api/session/transfer")
def session_transfer():
    if not authorized(): return json_error("غير مصرح", 401)
    try:
        transfer_session()
        return jsonify({"ok": True, "message": "تم ترحيل الجلسة إلى Render؛ ستتم إعادة تشغيل الخدمة تلقائيًا"})
    except (OSError, URLError, HTTPError, RuntimeError) as exc:
        return json_error(str(exc))


@app.post("/api/bot/start")
def bot_start():
    global _bot_process
    if not authorized(): return json_error("غير مصرح", 401)
    if _bot_process is not None and _bot_process.poll() is None:
        return jsonify({"ok": True, "message": "البوت يعمل حاليًا"})
    if not (os.getenv("SESSION_STRING") or os.getenv("TMD_SESSION_STRING") or os.getenv("TMD_SESSION_B64") or SESSION_FILE.exists()):
        return json_error("أكمل تسجيل جلسة Telegram أو رحّلها أولًا")
    _bot_process = subprocess.Popen([sys.executable, "-m", "Unlock"])
    return jsonify({"ok": True, "message": "تم تشغيل البوت"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    if os.getenv("SESSION_STRING") or os.getenv("TMD_SESSION_STRING") or os.getenv("TMD_SESSION_B64"):
        _bot_process = subprocess.Popen([sys.executable, "-m", "Unlock"])
    app.run(host="0.0.0.0", port=port, threaded=True)
