from __future__ import annotations

import base64
import hmac
import json
import os
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request, send_file

from bot_service import TelegramBotService
from config_store import APP_DIR, load_settings, save_settings, secret_state
from downloader import DownloadManager

IS_RENDER = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
HOST = os.environ.get("TMD_HOST", "0.0.0.0" if IS_RENDER else "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("TMD_PORT", "8765")))
OPEN_BROWSER = os.environ.get("TMD_OPEN_BROWSER", "false" if IS_RENDER else "true").lower() in {"1", "true", "yes"}
DASHBOARD_TOKEN = os.environ.get("TMD_DASHBOARD_TOKEN", "").strip()
_owner_raw = os.environ.get("TMD_SESSION_SETUP_OWNER_ID", "").strip()
try:
    SESSION_SETUP_OWNER_ID = int(_owner_raw) if _owner_raw else None
except ValueError:
    SESSION_SETUP_OWNER_ID = None
app = Flask(__name__)


@app.before_request
def protect_dashboard() -> Any:
    """Keep the dashboard and every control API private; only health is public."""
    if request.path == "/health" or request.path.startswith("/assets/"):
        return None
    if not DASHBOARD_TOKEN and IS_RENDER:
        response = jsonify({"error": "لوحة الإدارة غير مهيأة بمفتاح حماية"})
        response.status_code = 503
        return response
    if not DASHBOARD_TOKEN:
        return None

    supplied = request.headers.get("X-Dashboard-Token", "").strip()
    if not supplied:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        elif authorization.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(authorization[6:].strip()).decode("utf-8")
                supplied = decoded.split(":", 1)[1] if ":" in decoded else ""
            except (ValueError, UnicodeDecodeError):
                supplied = ""
    if supplied and hmac.compare_digest(supplied, DASHBOARD_TOKEN):
        return None

    response = jsonify({"error": "يلزم مفتاح لوحة الإدارة"})
    response.status_code = 401
    response.headers["WWW-Authenticate"] = 'Basic realm="Telegram Media Suite"'
    return response
log_lock = threading.Lock()
log_buffer: deque[str] = deque(maxlen=250)
settings = load_settings()


def log(message: str) -> None:
    safe = str(message)
    with log_lock:
        log_buffer.appendleft(f"{time.strftime('%H:%M:%S')} — {safe}")


def logs() -> list[str]:
    with log_lock:
        return list(log_buffer)


manager = DownloadManager(log=log)
def save_bot_allowlist(user_ids: set[int]) -> None:
    current = load_settings()
    current["allowed_user_ids"] = ",".join(str(user_id) for user_id in sorted(user_ids))
    save_settings(current)


bot = TelegramBotService(
    session_getter=lambda: manager.session,
    storage_getter=lambda: str(load_settings().get("storage_path", Path.home() / "TelegramDownloads")),
    log=log,
    allowlist_saver=save_bot_allowlist,
    self_enrollment_enabled=(os.environ.get("SELF_ENROLLMENT_ENABLED", "false" if IS_RENDER else "true").lower() in {"1", "true", "yes"}),
    session_setup_owner_id=SESSION_SETUP_OWNER_ID,
)


def public_state() -> dict[str, Any]:
    current = load_settings()
    return {
        "settings": {
            "api_id_set": bool(str(current.get("api_id", "")).strip()),
            "api_hash": secret_state(str(current.get("api_hash", ""))),
            "bot_token": secret_state(str(current.get("bot_token", ""))),
            "phone": secret_state(str(current.get("phone", ""))),
            "chat_id": current.get("chat_id", ""),
            "storage_path": current.get("storage_path", ""),
            "session_path": current.get("session_path", ""),
            "allowed_user_ids": current.get("allowed_user_ids", ""),
            "auto_start": bool(current.get("auto_start", False)),
        },
        "session": manager.session_snapshot(),
        "bot": {"status": bot.status, "error": bot.error},
        "jobs": manager.snapshot().get("jobs", {}),
        "logs": logs(),
    }


PAGE = r"""
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram Media Suite</title>
<style>
:root{font-family:"Segoe UI",Tahoma,sans-serif;color:#e8eef7;background:#0f172a}
*{box-sizing:border-box} body{margin:0;background:#07111f url('/assets/wolf_background.png') center/cover fixed;min-height:100vh;position:relative}body:before{content:"";position:fixed;inset:0;background:linear-gradient(90deg,#07111fee 0%,#07111fcf 48%,#07111f75 100%);pointer-events:none;z-index:0}
main{position:relative;z-index:1;max-width:1180px;margin:0 auto;margin-right:230px;padding:28px}.side-menu{position:fixed;z-index:2;right:18px;top:18px;bottom:18px;width:190px;padding:18px 12px;background:#0b1425dd;border:1px solid #334a6c;border-radius:20px;backdrop-filter:blur(16px);box-shadow:0 18px 60px #02061788}.side-menu h2{font-size:18px;margin:4px 8px 18px}.side-menu .brand-mark{font-size:30px;color:#8bd8ff;margin:0 8px 4px}.side-menu a{display:block;color:#dbeafe;text-decoration:none;padding:11px 12px;border-radius:10px;margin:5px 0;background:#17243a99;transition:.2s}.side-menu a:hover,.side-menu a:focus{background:#2563eb;color:#fff;transform:translateX(-3px)}.side-menu small{display:block;color:#91a4bd;margin:18px 8px 6px}
.hero{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:20px}
h1{margin:0;font-size:30px}.muted{color:#9fb0c7}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.card{background:#172033;border:1px solid #2c3a52;border-radius:16px;padding:18px;box-shadow:0 12px 40px #02061744}.wide{grid-column:1/-1}
label{display:block;margin:12px 0 6px;color:#b9c9df}input,button,select{font:inherit;border-radius:10px;border:1px solid #3a4b68;padding:10px;background:#0b1220;color:#eef5ff;width:100%}button{background:#2563eb;border:0;cursor:pointer;font-weight:700}button.secondary{background:#334155}button.danger{background:#b91c1c}.row{display:flex;gap:10px;align-items:end}.row>*{flex:1}.status{padding:10px;border-radius:10px;background:#0b1220;margin:8px 0}.ok{color:#86efac}.warn{color:#fde68a}.err{color:#fca5a5}.progress{height:15px;background:#0b1220;border-radius:20px;overflow:hidden;margin-top:8px}.bar{height:100%;background:linear-gradient(90deg,#38bdf8,#2563eb);width:0;transition:width .2s}.job{border-top:1px solid #334155;padding:12px 0}.job:first-child{border-top:0}.log{font-family:Consolas,monospace;white-space:pre-wrap;background:#070b14;padding:12px;border-radius:10px;max-height:260px;overflow:auto;direction:ltr;text-align:left}.pill{display:inline-block;padding:4px 8px;border-radius:99px;background:#334155;margin:2px;font-size:12px}
@media(max-width:820px){.side-menu{position:relative;right:auto;top:auto;bottom:auto;width:auto;margin:12px;display:flex;gap:6px;overflow:auto}.side-menu h2,.side-menu small,.side-menu .brand-mark{display:none}.side-menu a{white-space:nowrap}.grid{grid-template-columns:1fr}main{margin-right:0;padding:16px}.wide{grid-column:auto}.hero{display:block}}
</style></head>
<body><aside class="side-menu"><div class="brand-mark">◈</div><h2>القائمة الرئيسية</h2><a href="#account">إعداد الحساب</a><a href="#auth">تسجيل الدخول</a><a href="#download">تنزيل رابط</a><a href="#bot">البوت</a><a href="#jobs">المهام</a><a href="#logs">السجل</a><small>تنقل سريع بين الأقسام</small></aside><main>
<section class="hero">
<div><h1>Telegram Media Suite</h1><p class="muted">تنزيل منظم حسب القناة، جلسة محفوظة، وبوت Telegram في واجهة واحدة.</p></div><div id="topStatus" class="pill">جاهز</div></section>
<div class="grid">
<section id="account" class="card"><h2>إعداد الحساب</h2>
<p class="muted">لا تظهر القيم السرية بعد حفظها. تُحفظ محليًا في ملف env.</p>
<label>api_id</label><input id="api_id" placeholder="رقم التطبيق">
<label>api_hash</label><input id="api_hash" type="password" placeholder="اتركه فارغًا إذا كان محفوظًا">
<label>Bot Token</label><input id="bot_token" type="password" placeholder="اختياري لتشغيل البوت">
<label>رقم الهاتف</label><input id="phone" placeholder="للحساب الشخصي">
<label>معرّف الدردشة الافتراضي</label><input id="chat_id" placeholder="مثال: -1001234567890">
<div class="row"><button onclick="saveSettings()">حفظ الإعدادات</button><button class="secondary" onclick="startSession()">بدء جلسة Telegram</button></div>
<div id="sessionStatus" class="status">حالة الجلسة: ...</div></section>

<section id="auth" class="card"><h2>تسجيل الدخول</h2>
<p class="muted">تظهر الخانة المطلوبة فقط عندما يطلبها Telegram.</p>
<div id="authPrompt" class="status">لا توجد مطالبة حاليًا.</div><input id="authValue" type="password" placeholder="أدخل القيمة المطلوبة">
<div class="row"><button onclick="sendAuth('phone')">إرسال رقم الهاتف</button><button onclick="sendAuth('code')">إرسال الرمز</button><button onclick="sendAuth('password')">إرسال كلمة المرور</button></div>
<p class="muted">لا تُحفظ كلمة المرور أو رمز التحقق في الإعدادات.</p></section>

<section id="download" class="card"><h2>تنزيل رابط</h2>
<label>رابط رسالة Telegram</label><input id="link" placeholder="https://t.me/channel/123 أو رابط خاص">
<label>مسار التخزين</label><input id="storage_path" placeholder="سيظهر بعد حفظ الإعدادات">
<button onclick="downloadLink()">تنزيل وتنظيم الملف</button><div id="downloadStatus" class="status">لم تبدأ مهمة.</div></section>

<section id="bot" class="card"><h2>البوت</h2>
<label>User IDs المسموح لهم، مفصولة بفواصل</label><input id="allowed_user_ids" placeholder="123456789,987654321">
<label><input id="auto_start" type="checkbox" style="width:auto"> تشغيل البوت تلقائيًا عند فتح البرنامج</label>
<div class="row"><button onclick="startBot()">تشغيل البوت</button><button class="danger" onclick="stopBot()">إيقاف البوت</button></div><div id="botStatus" class="status">حالة البوت: ...</div>
<p class="muted">البوت يعالج الروابط من المستخدمين المصرح لهم فقط، ويرسل معلومات القناة والملف الناتج.</p></section>

<section id="jobs" class="card wide"><h2>المهام</h2>
<div id="jobsList">لا توجد مهام.</div></section>
<section id="logs" class="card wide"><h2>السجل</h2>
<div id="logsBox" class="log">لا يوجد سجل بعد.</div></section>
</div></main>
<script>
let state={};
async function api(url, options={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...options});const d=await r.json();if(!r.ok)throw new Error(d.error||'خطأ غير معروف');return d}
function setValue(id,v){const e=document.getElementById(id);if(v!==undefined&&v!==null)e.value=v}
function render(s){state=s;const ss=s.session||{};document.getElementById('sessionStatus').textContent='حالة الجلسة: '+ss.state+(ss.error?' — '+ss.error:'');document.getElementById('botStatus').textContent='حالة البوت: '+(s.bot||{}).status;
setValue('chat_id',s.settings.chat_id);setValue('storage_path',s.settings.storage_path);setValue('allowed_user_ids',s.settings.allowed_user_ids);document.getElementById('auto_start').checked=!!s.settings.auto_start;
const jobs=Object.values(s.jobs||{});document.getElementById('jobsList').innerHTML=jobs.length?jobs.map(j=>{const p=j.total?Math.floor(j.downloaded*100/j.total):0;return `<div class="job"><b>${j.status}</b> — ${j.file||j.link}<div class="progress"><div class="bar" style="width:${p}%"></div></div><span class="muted">${j.downloaded||0} / ${j.total||0} bytes</span>${j.error?`<div class="err">${j.error}</div>`:''}</div>`}).join(''):'لا توجد مهام.';
document.getElementById('logsBox').textContent=(s.logs||[]).join('\n')||'لا يوجد سجل بعد.';
const prompt=ss.state==='phone'?'أدخل رقم الهاتف ثم أرسل':ss.state==='code'?'أدخل رمز Telegram ثم أرسل':ss.state==='password'?'أدخل كلمة مرور التحقق بخطوتين ثم أرسل':'لا توجد مطالبة حاليًا.';document.getElementById('authPrompt').textContent=prompt;document.getElementById('topStatus').textContent=ss.state==='ready'?'الجلسة جاهزة':(s.bot||{}).status==='running'?'البوت يعمل':'جاهز'}
async function refresh(){try{render(await api('/api/state'))}catch(e){document.getElementById('topStatus').textContent=e.message}}
async function saveSettings(){const body={api_id:api_id.value,api_hash:api_hash.value,bot_token:bot_token.value,phone:phone.value,chat_id:chat_id.value,storage_path:storage_path.value,allowed_user_ids:allowed_user_ids.value,auto_start:auto_start.checked};try{await api('/api/settings',{method:'POST',body:JSON.stringify(body)});api_hash.value='';bot_token.value='';phone.value='';alert('تم حفظ الإعدادات محليًا');refresh()}catch(e){alert(e.message)}}
async function startSession(){try{await api('/api/session/start',{method:'POST'});refresh()}catch(e){alert(e.message)}}
async function sendAuth(kind){try{await api('/api/auth',{method:'POST',body:JSON.stringify({kind,value:authValue.value})});authValue.value='';refresh()}catch(e){alert(e.message)}}
async function downloadLink(){try{const d=await api('/api/download',{method:'POST',body:JSON.stringify({link:link.value})});document.getElementById('downloadStatus').textContent='تم إنشاء المهمة: '+d.job_id;refresh()}catch(e){alert(e.message)}}
async function startBot(){try{await api('/api/bot/start',{method:'POST'});refresh()}catch(e){alert(e.message)}}
async function stopBot(){try{await api('/api/bot/stop',{method:'POST'});refresh()}catch(e){alert(e.message)}}
setInterval(refresh,1500);refresh();
</script></body></html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/assets/wolf_background.png")
def wolf_background():
    asset = Path(__file__).resolve().parent.parent / "assets" / "wolf_background.png"
    return send_file(asset, mimetype="image/png")


@app.get("/api/state")
def api_state():
    return jsonify(public_state())


@app.get("/health")
def health():
    session_state = manager.session_snapshot().get("state", "not_started")
    return jsonify({
        "ok": True,
        "service": "telegram-media-suite",
        "version": "3.2.2",
        "session": session_state,
        "bot": bot.status,
    })


@app.post("/api/settings")
def api_settings():
    payload = request.get_json(silent=True) or {}
    global settings
    settings = save_settings(payload)
    log("تم حفظ الإعدادات محليًا، وتم إخفاء الأسرار من السجل.")
    return jsonify({"ok": True, "state": public_state()})


@app.post("/api/session/start")
def api_session_start():
    current = load_settings()
    if not str(current.get("api_id", "")).strip() or not str(current.get("api_hash", "")).strip():
        return jsonify({"error": "أدخل api_id وapi_hash واحفظ الإعدادات أولًا"}), 400
    manager.configure_session(str(current["api_id"]), str(current["api_hash"]), str(current["session_path"]))
    return jsonify({"ok": True})


@app.post("/api/auth")
def api_auth():
    payload = request.get_json(silent=True) or {}
    kind, value = str(payload.get("kind", "")), str(payload.get("value", ""))
    if kind not in {"phone", "code", "password"} or not value:
        return jsonify({"error": "إدخال التحقق غير صالح"}), 400
    result = manager.submit_auth(kind, value)
    return jsonify({"ok": True, "message": result})


@app.post("/api/download")
def api_download():
    payload = request.get_json(silent=True) or {}
    current = load_settings()
    ok, result = manager.submit_download(str(payload.get("link", "")), str(current["storage_path"]))
    if not ok:
        return jsonify({"error": result}), 400
    return jsonify({"ok": True, "job_id": result})


@app.post("/api/bot/start")
def api_bot_start():
    current = load_settings()
    ok, message = bot.start(str(current.get("api_id", "")), str(current.get("api_hash", "")),
                            str(current.get("bot_token", "")), str(current.get("allowed_user_ids", "")))
    if not ok:
        return jsonify({"error": message}), 400
    return jsonify({"ok": True, "message": message})


@app.post("/api/bot/stop")
def api_bot_stop():
    bot.stop()
    return jsonify({"ok": True})


def _auto_start_bot(current: dict[str, Any]) -> None:
    # عند وجود مالك إعداد، يبدأ Bot API أولًا حتى يستطيع المالك إرسال /start
    # وإكمال مصادقة جلسة الحساب الشخصي من داخل البوت.
    if SESSION_SETUP_OWNER_ID:
        ok, message = bot.start(
            str(current.get("api_id", "")), str(current.get("api_hash", "")),
            str(current.get("bot_token", "")), str(current.get("allowed_user_ids", "")),
        )
        if not ok:
            log(f"تعذر تشغيل وضع إعداد الجلسة: {message}")
        return
    for _ in range(60):
        if manager.session_snapshot().get("state") == "ready":
            bot.start(str(current.get("api_id", "")), str(current.get("api_hash", "")),
                      str(current.get("bot_token", "")), str(current.get("allowed_user_ids", "")))
            return
        time.sleep(0.5)
    log("تعذر التشغيل التلقائي للبوت لأن جلسة الحساب لم تصبح جاهزة.")


_runtime_started = False
_runtime_lock = threading.Lock()


def initialize_runtime() -> None:
    global _runtime_started
    with _runtime_lock:
        if _runtime_started:
            return
        _runtime_started = True
    current = load_settings()
    if str(current.get("api_id", "")).strip() and str(current.get("api_hash", "")).strip():
        manager.configure_session(str(current["api_id"]), str(current["api_hash"]), str(current["session_path"]))
    if current.get("auto_start") and current.get("bot_token"):
        threading.Thread(target=_auto_start_bot, args=(current,), daemon=True, name="auto-start-bot").start()
    log(f"واجهة الويب تعمل على http://{HOST}:{PORT}")


initialize_runtime()


def start_app() -> None:
    if OPEN_BROWSER:
        webbrowser.open_new(f"http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    start_app()
