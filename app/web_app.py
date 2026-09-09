from __future__ import annotations

import base64
import hmac
import io
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
from downloader import DownloadManager, extract_telegram_links

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
label{display:block;margin:12px 0 6px;color:#b9c9df}input,textarea,button,select{font:inherit;border-radius:10px;border:1px solid #3a4b68;padding:10px;background:#0b1220;color:#eef5ff;width:100%}button{background:#2563eb;border:0;cursor:pointer;font-weight:700}button.secondary{background:#334155}button.danger{background:#b91c1c}.row{display:flex;gap:10px;align-items:end}.row>*{flex:1}.status{padding:10px;border-radius:10px;background:#0b1220;margin:8px 0}.ok{color:#86efac}.warn{color:#fde68a}.err{color:#fca5a5}.progress{height:15px;background:#0b1220;border-radius:20px;overflow:hidden;margin-top:8px;position:relative}.bar{height:100%;background:linear-gradient(90deg,#38bdf8,#2563eb,#60a5fa);background-size:200% 100%;width:0;transition:width .35s ease;animation:bar-flow 1.4s linear infinite}.bar.done{background:linear-gradient(90deg,#22c55e,#86efac);animation:none}@keyframes bar-flow{from{background-position:0 0}to{background-position:200% 0}}.job{border-top:1px solid #334155;padding:14px 0;position:relative}.job:first-child{border-top:0}.job-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.job-status{padding:4px 9px;border-radius:999px;background:#24344f;color:#bfdbfe;font-size:12px}.job-status.completed{background:#14532d;color:#bbf7d0}.job-status.failed{background:#7f1d1d;color:#fecaca}.job-meta{display:flex;justify-content:space-between;gap:10px;margin-top:7px;font-size:12px}.job-layer{position:absolute;inset:8px 0 auto 0;height:2px;background:linear-gradient(90deg,transparent,#38bdf8,transparent);opacity:.55;pointer-events:none;animation:layer-sweep 2.2s ease-in-out infinite}@keyframes layer-sweep{0%,100%{transform:translateX(-45%);opacity:.15}50%{transform:translateX(45%);opacity:.8}}.download-summary{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.download-summary .pill{margin:0}.log{font-family:Consolas,monospace;white-space:pre-wrap;background:#070b14;padding:12px;border-radius:10px;max-height:260px;overflow:auto;direction:ltr;text-align:left}.pill{display:inline-block;padding:4px 8px;border-radius:99px;background:#334155;margin:2px;font-size:12px}
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
<div class="row"><button class="secondary" onclick="restoreSession()">استعادة الجلسة السابقة من Render</button><button class="secondary" onclick="exportSession()">ترحيل الجلسة إلى Render</button><button class="danger" onclick="resetSession()">إزالة جلسة Render وبدء جديدة</button></div>
<p class="muted">الترحيل ينزّل ملفًا بترميز Base64 لتضعه يدويًا في <code>TMD_SESSION_B64</code>. لن تُحذف الجلسة تلقائيًا عند تحديث النظام.</p>
<div id="sessionStatus" class="status">حالة الجلسة: ...</div></section>

<section id="auth" class="card"><h2>تسجيل الدخول</h2>
<p class="muted">تظهر الخانة المطلوبة فقط عندما يطلبها Telegram.</p>
<div id="authPrompt" class="status">لا توجد مطالبة حاليًا.</div><input id="authValue" type="password" placeholder="أدخل القيمة المطلوبة">
<div class="row"><button onclick="requestSavedPhone()">طلب الرمز للرقم المحفوظ</button><button onclick="resendCode()">إعادة إرسال الرمز</button><button onclick="sendAuth('phone')">إرسال رقم الهاتف</button><button onclick="sendAuth('code')">إرسال الرمز</button><button onclick="sendAuth('password')">إرسال كلمة المرور</button></div>
<p class="muted">لا تُحفظ كلمة المرور أو رمز التحقق في الإعدادات.</p></section>

<section id="download" class="card"><h2>تنزيل رابط</h2>
<label>روابط رسائل Telegram</label><textarea id="link" rows="4" placeholder="ضع رابطًا واحدًا أو عدة روابط، كل رابط في سطر مستقل"></textarea>
<label>مسار التخزين</label><input id="storage_path" placeholder="سيظهر بعد حفظ الإعدادات">
<button onclick="downloadLink()">تنزيل وتنظيم الملفات</button><div id="downloadStatus" class="status">لم تبدأ مهمة.</div></section>

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
async function api(url, options={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...options});const raw=await r.text();let d=null;try{d=raw?JSON.parse(raw):null}catch(_e){}if(!r.ok){const e=new Error(d&&d.error?d.error:raw.trim().startsWith('<')?'خدمة Render تستيقظ أو تعيد النشر الآن.':`تعذر تنفيذ الطلب (${r.status})`);e.status=r.status;e.retryable=raw.trim().startsWith('<')||r.status===502||r.status===503;throw e}if(!d)throw new Error('الخادم أعاد ردًا غير صالح؛ أعد المحاولة بعد لحظات.');return d}
function setValue(id,v){const e=document.getElementById(id);if(v!==undefined&&v!==null)e.value=v}
function render(s){state=s;const ss=s.session||{};const sessionLabel=ss.state==='ready'&&ss.origin==='saved_session'?'جاهزة — جلسة محفوظة محمّلة تلقائيًا':ss.state==='ready'&&ss.origin==='new_login'?'جاهزة — تم تسجيلها الآن':ss.state;document.getElementById('sessionStatus').textContent='حالة الجلسة: '+sessionLabel+(ss.error?' — '+ss.error:'');document.getElementById('botStatus').textContent='حالة البوت: '+(s.bot||{}).status;
setValue('chat_id',s.settings.chat_id);setValue('storage_path',s.settings.storage_path);setValue('allowed_user_ids',s.settings.allowed_user_ids);document.getElementById('auto_start').checked=!!s.settings.auto_start;
const jobs=Object.values(s.jobs||{});const counts=jobs.reduce((a,j)=>(a[j.status]=(a[j.status]||0)+1,a),{});document.getElementById('jobsList').innerHTML=jobs.length?`<div class="download-summary"><span class="pill">الكل: ${jobs.length}</span><span class="pill">قيد العمل: ${(counts.downloading||0)+(counts.queued||0)}</span><span class="pill">مكتمل: ${counts.completed||0}</span><span class="pill">فشل: ${counts.failed||0}</span></div>`+jobs.map(j=>{const p=j.total?Math.min(100,Math.floor(j.downloaded*100/j.total)):j.status==='completed'?100:0;const label={queued:'في الانتظار',downloading:'جارٍ التنزيل',completed:'اكتمل',failed:'فشل'}[j.status]||j.status;return `<div class="job"><div class="job-layer"></div><div class="job-head"><b>${j.file||j.link}</b><span class="job-status ${j.status}">${label}</span></div><div class="progress"><div class="bar ${j.status==='completed'?'done':''}" style="width:${p}%"></div></div><div class="job-meta"><span class="muted">${p}% — ${j.downloaded||0} / ${j.total||0} bytes</span><span class="muted">${j.id.slice(0,8)}</span></div>${j.error?`<div class="err">${j.error}</div>`:''}</div>`}).join(''):'لا توجد مهام.';
document.getElementById('logsBox').textContent=(s.logs||[]).join('\n')||'لا يوجد سجل بعد.';
const prompt=ss.state==='phone'?'أدخل رقم الهاتف ثم أرسل':ss.state==='code'?'أدخل رمز Telegram ثم أرسل':ss.state==='password'?'أدخل كلمة مرور التحقق بخطوتين ثم أرسل':ss.state==='ready'&&ss.origin==='saved_session'?'الجلسة المحفوظة جاهزة؛ لم يُطلب رقم أو رمز جديد.':'لا توجد مطالبة حاليًا.';document.getElementById('authPrompt').textContent=prompt;document.getElementById('topStatus').textContent=ss.state==='ready'?(ss.origin==='saved_session'?'جلسة محفوظة جاهزة':'الجلسة جاهزة'):(s.bot||{}).status==='running'?'البوت يعمل':'جاهز'}
async function refresh(){try{render(await api('/api/state'))}catch(e){document.getElementById('topStatus').textContent=e.message}}
async function saveSettings(){const body={api_id:api_id.value,api_hash:api_hash.value,bot_token:bot_token.value,phone:phone.value,chat_id:chat_id.value,storage_path:storage_path.value,allowed_user_ids:allowed_user_ids.value,auto_start:auto_start.checked};try{await api('/api/settings',{method:'POST',body:JSON.stringify(body)});api_hash.value='';bot_token.value='';phone.value='';alert('تم حفظ الإعدادات محليًا');refresh()}catch(e){alert(e.message)}}
async function startSession(){try{await api('/api/session/start',{method:'POST'});refresh()}catch(e){alert(e.message)}}
async function restoreSession(){const button=document.querySelector('button[onclick="restoreSession()"]');if(button)button.disabled=true;try{let last=null;for(let attempt=1;attempt<=10;attempt++){try{const d=await api('/api/session/restore',{method:'POST'});alert(d.message||'بدأت استعادة الجلسة السابقة');refresh();return}catch(e){last=e;if(!e.retryable)throw e;if(button)button.textContent=`جارٍ استيقاظ Render (${attempt}/10)`;await new Promise(resolve=>setTimeout(resolve,3000))}}throw last||new Error('تعذر استعادة الجلسة')}catch(e){alert(e.message||'تعذر استعادة الجلسة')}finally{if(button){button.disabled=false;button.textContent='استعادة الجلسة السابقة من Render'}}}
async function exportSession(){try{const r=await fetch('/api/session/export');if(!r.ok){const raw=await r.text();let d=null;try{d=JSON.parse(raw)}catch(_e){}throw new Error(d?.error||(raw.trim().startsWith('<')?'خدمة Render تستيقظ أو تعيد النشر الآن؛ أعد المحاولة بعد قليل.':'تعذر تصدير الجلسة'))}const blob=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='tmd_user.session.b64';a.click();URL.revokeObjectURL(a.href);alert('تم تنزيل ملف الجلسة. استخدم محتواه في TMD_SESSION_B64 على Render.')}catch(e){alert(e.message)}}
async function resetSession(){if(!confirm('سيؤدي هذا إلى حذف ملف جلسة Telegram الحالي وطلب تسجيل دخول جديد. هل تريد المتابعة؟'))return;try{const d=await api('/api/session/reset',{method:'POST'});alert(d.message||'بدأت جلسة جديدة؛ أدخل Phone الآن');refresh()}catch(e){alert(e.message)}}
async function requestSavedPhone(){try{await api('/api/session/request-phone',{method:'POST'});refresh()}catch(e){alert(e.message)}}
async function resendCode(){try{await api('/api/session/resend-code',{method:'POST'});refresh()}catch(e){alert(e.message)}}
async function sendAuth(kind){try{await api('/api/auth',{method:'POST',body:JSON.stringify({kind,value:authValue.value})});authValue.value='';refresh()}catch(e){alert(e.message)}}
async function downloadLink(){try{const d=await api('/api/download',{method:'POST',body:JSON.stringify({link:link.value})});document.getElementById('downloadStatus').textContent='تم إنشاء '+d.job_ids.length+' مهمة تنزيل متوازية.';link.value='';refresh()}catch(e){alert(e.message)}}
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


@app.get("/api/session/export")
def api_session_export():
    ok, encoded = manager.export_session()
    if not ok:
        return jsonify({"error": encoded}), 400
    return send_file(
        io.BytesIO(encoded.encode("ascii")),
        mimetype="text/plain",
        as_attachment=True,
        download_name="tmd_user.session.b64",
    )


@app.post("/api/session/restore")
def api_session_restore():
    current = load_settings()
    ok, message = manager.restore_session_from_env(
        str(current.get("api_id", "")).strip(),
        str(current.get("api_hash", "")).strip(),
        str(current.get("session_path", "")),
    )
    if not ok:
        return jsonify({"error": message}), 400
    threading.Thread(target=_auto_start_bot, args=(current,), daemon=True, name="restore-session-bot").start()
    log("بدأت استعادة الجلسة السابقة من Render؛ سيبدأ البوت تلقائيًا بعد جاهزية الجلسة.")
    return jsonify({"ok": True, "message": message})


@app.post("/api/session/reset")
def api_session_reset():
    ok, message = manager.reset_session()
    if not ok:
        return jsonify({"error": message}), 400
    log("تم تنفيذ إزالة جلسة Telegram بطلب صريح وبدأت جلسة جديدة؛ لم تُحذف أي جلسة تلقائيًا.")
    return jsonify({"ok": True, "message": message})


@app.post("/api/session/request-phone")
def api_session_request_phone():
    current = load_settings()
    phone = str(current.get("phone", "")).strip()
    if not phone:
        return jsonify({"error": "رقم الهاتف غير محفوظ في الإعدادات"}), 400
    result = manager.submit_auth("phone", phone)
    return jsonify({"ok": True, "message": result})


@app.post("/api/session/resend-code")
def api_session_resend_code():
    result = manager.resend_auth_code()
    return jsonify({"ok": True, "message": result})


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
    raw_links = payload.get("links", payload.get("link", ""))
    if isinstance(raw_links, list):
        text = "\n".join(str(item) for item in raw_links)
    else:
        text = str(raw_links)
    links = extract_telegram_links(text, limit=20)
    if not links:
        return jsonify({"error": "أدخل رابط Telegram واحدًا على الأقل، كل رابط في سطر مستقل"}), 400
    job_ids: list[str] = []
    errors: list[str] = []
    for link in links:
        ok, result = manager.submit_download(link, str(current["storage_path"]))
        if ok:
            job_ids.append(result)
        else:
            errors.append(f"{link}: {result}")
    if not job_ids:
        return jsonify({"error": errors[0] if errors else "تعذر إنشاء مهام التنزيل"}), 400
    return jsonify({"ok": True, "job_ids": job_ids, "errors": errors})


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


def _auto_request_phone(current: dict[str, Any]) -> None:
    """Request the Telegram login code once when a saved phone is available."""
    time.sleep(1.0)
    if manager.session_snapshot().get("state") != "phone":
        return
    phone = str(current.get("phone", "")).strip()
    if not phone:
        log("لا يوجد رقم هاتف محفوظ لبدء طلب رمز Telegram.")
        return
    manager.submit_auth("phone", phone)
    log("تم طلب رمز Telegram تلقائيًا من الرقم المحفوظ؛ أدخل الرمز من الواجهة عند وصوله.")


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
    if os.environ.get("TMD_AUTO_REQUEST_PHONE", "false").lower() in {"1", "true", "yes"}:
        threading.Thread(target=_auto_request_phone, args=(current,), daemon=True, name="auto-request-phone").start()
    log(f"واجهة الويب تعمل على http://{HOST}:{PORT}")


initialize_runtime()


def start_app() -> None:
    if OPEN_BROWSER:
        webbrowser.open_new(f"http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    start_app()
