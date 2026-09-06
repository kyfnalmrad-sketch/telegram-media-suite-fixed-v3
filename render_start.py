from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import subprocess
import sys
import threading
import time
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
_bot_started_at: float | None = None
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


def bot_process_snapshot() -> dict:
    running = _bot_process is not None and _bot_process.poll() is None
    return {
        "running": running,
        "pid": _bot_process.pid if running else None,
        "exit_code": None if running or _bot_process is None else _bot_process.poll(),
        "uptime_seconds": int(time.time() - _bot_started_at) if running and _bot_started_at else 0,
    }


def service_snapshot() -> dict:
    payload = read_jobs()
    jobs = list(payload.get("jobs", {}).values())
    counts: dict[str, int] = {}
    for job in jobs:
        status = job.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    bot = bot_process_snapshot()
    session = setup.snapshot()
    source_configured = bool(env_first("TELEGRAM_SESSION_STRING", "SESSION_STRING", "TMD_SESSION_STRING", "TMD_SESSION_B64") or SESSION_FILE.exists())
    if session.get("state") == "idle" and source_configured:
        session["state"] = "ready"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage_writable = os.access(DATA_DIR, os.W_OK)
    except OSError:
        storage_writable = False
    return {
        "ok": True,
        "render": {
            "service": "running",
            "port": int(os.getenv("PORT", "10000")),
            "data_dir": str(DATA_DIR),
            "storage_writable": storage_writable,
            "checked_at": int(time.time()),
        },
        "bot": bot,
        "worker": {
            "state": "running" if bot["running"] else "stopped",
            "description": "عامل العمليات يعمل داخل عملية البوت" if bot["running"] else "شغّل البوت لبدء عامل العمليات",
        },
        "session": {
            **session,
            "source_configured": source_configured,
        },
        "queue": {
            "total": len(jobs),
            "counts": counts,
            "latest": max(jobs, key=lambda job: job.get("updated_at", 0), default=None),
        },
    }


@app.get("/health")
def health():
    return jsonify(service_snapshot())


@app.get("/")
def dashboard():
    phone = env_first("TELEGRAM_PHONE_NUMBER", "PHONE") or "غير مضبوط"
    html = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram Media Suite</title>
<style>
:root{color-scheme:dark;--bg:#0b1220;--panel:#121d31;--panel2:#18263d;--line:#2b3c59;--text:#e8eef8;--muted:#9fb0c8;--blue:#55a8ff;--green:#36d399;--red:#ff6b7a;--amber:#ffc857}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#09111f,#111d31 55%,#0b1425);color:var(--text);font-family:Tahoma,Arial,sans-serif;line-height:1.6}.wrap{max-width:1180px;margin:0 auto;padding:28px 18px}.header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px}.brand h1{margin:0;font-size:28px}.brand p{margin:4px 0 0;color:var(--muted)}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--amber);margin-left:7px}.dot.ok{background:var(--green)}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}.card,.panel{background:rgba(18,29,49,.92);border:1px solid var(--line);border-radius:16px;box-shadow:0 12px 28px #0003}.card{padding:16px}.card .label{color:var(--muted);font-size:13px}.card .value{font-size:23px;font-weight:700;margin-top:4px}.panel{padding:18px;margin-bottom:18px}.panel h2{font-size:18px;margin:0 0 14px}.actions{display:flex;flex-wrap:wrap;gap:8px}.actions button,button{border:1px solid #3a5a80;background:#1d3858;color:var(--text);border-radius:9px;padding:10px 14px;cursor:pointer}.actions button:hover,button:hover{background:#28527d}.danger{border-color:#843d4a!important}.forms{display:grid;grid-template-columns:1fr 1fr;gap:16px}.formbox{background:var(--panel2);padding:14px;border-radius:12px}.formbox h3{margin:0 0 8px;font-size:15px}.formbox input{width:100%;padding:10px;background:#0b1526;color:var(--text);border:1px solid var(--line);border-radius:8px;margin:5px 0 8px}.hint,#result{color:var(--muted);font-size:13px}.job{border:1px solid var(--line);border-radius:12px;padding:14px;margin:10px 0;background:#0e192b}.jobtop{display:flex;justify-content:space-between;gap:12px}.phase{color:var(--blue);font-weight:700}.meta{color:var(--muted);font-size:13px}.bar{height:9px;background:#263650;border-radius:20px;overflow:hidden;margin:10px 0}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--green));border-radius:20px}.jobactions{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.jobactions button{padding:6px 10px;font-size:12px}.log{font-family:ui-monospace,monospace;background:#091321;border-radius:9px;padding:10px;white-space:pre-wrap;color:#b9c9df;font-size:12px;max-height:180px;overflow:auto}.error{color:var(--red)}.success{color:var(--green)}@media(max-width:820px){.grid{grid-template-columns:repeat(2,1fr)}.forms{grid-template-columns:1fr}}@media(max-width:480px){.grid{grid-template-columns:1fr}.header{display:block}}
</style></head>
<body><main class="wrap">
<header class="header"><div class="brand"><h1>Telegram Media Suite</h1><p><span id="botDot" class="dot"></span>لوحة العمليات والمراقبة الحية</p></div><div id="lastUpdate" class="hint">آخر تحديث: —</div></header>
<section class="grid"><div class="card"><div class="label">خدمة Render</div><div id="renderState" class="value">جارٍ التحقق</div><div id="renderMeta" class="hint">—</div></div><div class="card"><div class="label">حالة البوت</div><div id="botState" class="value">جارٍ التحقق</div><div id="botMeta" class="hint">—</div></div><div class="card"><div class="label">عامل العمليات</div><div id="workerState" class="value">—</div><div id="workerMeta" class="hint">—</div></div><div class="card"><div class="label">جلسة Telegram</div><div id="sessionState" class="value">—</div><div id="sessionMeta" class="hint">—</div></div><div class="card"><div class="label">تخزين الحالة</div><div id="storageState" class="value">—</div><div id="storageMeta" class="hint">—</div></div><div class="card"><div class="label">العمليات الجارية</div><div id="activeCount" class="value">0</div><div id="totalCount" class="hint">إجمالي: 0</div></div></section>
<section class="panel"><h2>إعداد الجلسة وتشغيل البوت</h2><p class="hint">رقم الهاتف المضبوط في Render: <b>__PHONE__</b></p><div class="forms"><div class="formbox"><h3>1. بدء جلسة Telegram</h3><button onclick="startSession()">بدء إرسال الكود</button><input id="code" inputmode="numeric" placeholder="كود Telegram" autocomplete="one-time-code"><button onclick="submitCode()">تحقق من الكود</button><input id="password" type="password" placeholder="كلمة مرور التحقق بخطوتين"><button onclick="submitPassword()">تحقق من كلمة المرور</button></div><div class="formbox"><h3>2. الترحيل والتشغيل</h3><p class="hint">بعد نجاح الجلسة اضغط ترحيل الجلسة، ثم شغّل البوت.</p><div class="actions"><button onclick="transfer()">ترحيل الجلسة</button><button onclick="startBot()">تشغيل البوت</button></div><p id="result"></p></div></div></section>
<section class="panel"><div class="jobtop"><h2>العمليات الحية</h2><button onclick="loadAll()">تحديث الآن</button></div><div id="jobs">جارٍ تحميل العمليات...</div></section>
<section class="panel"><h2>السجل الحي لآخر عملية</h2><div id="liveLog" class="log">لا توجد أحداث بعد.</div></section>
</main>
<script>
const esc=(v)=>String(v??'').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const bytes=(n)=>{n=Number(n||0);const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<4){n/=1024;i++}return (i?n.toFixed(1):Math.round(n))+' '+u[i]};
const statusName={ready:'جاهز للجلب',queued:'في الانتظار',processing:'قيد المعالجة',downloading:'جاري الجلب',uploading:'جاري الإرسال',completed:'اكتمل',failed:'فشل',cancelled:'أُلغي'};
function jobButtons(j){let a='';if(j.status==='ready')a+='<button onclick="fetchJob('+j.id+')">جلب إلى البوت</button>';if(['queued','processing','downloading','uploading','paused'].includes(j.status))a+='<button class="danger" onclick="cancelJob('+j.id+')">إلغاء</button>';if(['failed','cancelled'].includes(j.status))a+='<button onclick="retryJob('+j.id+')">إعادة المحاولة</button>';return a+'<button onclick="showLog('+j.id+')">عرض السجل</button>'}
function renderJobs(data){const rows=Object.values(data.jobs||{}).sort((a,b)=>b.id-a.id).slice(0,20);document.getElementById('jobs').innerHTML=rows.length?rows.map(j=>{let p=Math.max(0,Math.min(100,Number(j.progress||0)));let total=Number(j.total||j.size||0);return '<article class="job"><div class="jobtop"><b>العملية #'+j.id+'</b><span class="phase">'+esc(statusName[j.status]||j.status)+'</span></div><div>'+esc(j.phase||'—')+'</div><div class="bar"><i style="width:'+p+'%"></i></div><div class="meta">'+p+'%'+(total?' • '+bytes(j.current||0)+' / '+bytes(total):'')+(j.speed?' • '+bytes(j.speed)+'/ث':'')+' • آخر تحديث: '+new Date((j.updated_at||0)*1000).toLocaleTimeString()+'</div>'+(j.error?'<div class="error">السبب: '+esc(j.error)+'</div><div class="hint">الحل: '+esc(j.error_solution||'إعادة المحاولة')+'</div>':'')+'<div class="jobactions">'+jobButtons(j)+'</div></article>'}).join(''):'<p class="hint">لا توجد عمليات.</p>';window.jobsCache=data.jobs||{}}
function showLog(id){const j=window.jobsCache[id];if(!j)return;document.getElementById('liveLog').textContent=(j.events||[]).slice(-30).join('\n')||'لا توجد أحداث مسجلة.';window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'})}
async function post(url,body={}){try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const x=await r.json();document.getElementById('result').textContent=x.error||x.message||'تم التنفيذ';await loadAll()}catch(e){document.getElementById('result').textContent='تعذر الاتصال بالخدمة'}}
async function startSession(){await post('/api/session/start')};async function submitCode(){await post('/api/session/code',{value:document.getElementById('code').value})};async function submitPassword(){await post('/api/session/password',{value:document.getElementById('password').value})};async function transfer(){await post('/api/session/transfer')};async function startBot(){await post('/api/bot/start')};
async function fetchJob(id){await post('/api/jobs/'+id+'/fetch')};async function cancelJob(id){await post('/api/jobs/'+id+'/cancel')};async function retryJob(id){await post('/api/jobs/'+id+'/retry')};
async function loadAll(){const [s,j]=await Promise.all([fetch('/api/status'),fetch('/api/jobs')]);if(s.ok){const x=await s.json();const active=Object.entries(x.queue.counts||{}).filter(([k])=>['queued','processing','downloading','uploading'].includes(k)).reduce((a,[,v])=>a+v,0);document.getElementById('renderState').textContent=x.render.service==='running'?'يعمل':'متوقف';document.getElementById('renderMeta').textContent='منفذ '+x.render.port;document.getElementById('botState').textContent=x.bot.running?'يعمل':'متوقف';document.getElementById('botDot').className='dot '+(x.bot.running?'ok':'');document.getElementById('botMeta').textContent=x.bot.running?'PID '+x.bot.pid+' • '+x.bot.uptime_seconds+'ث':'شغّل البوت من الزر أدناه';document.getElementById('workerState').textContent=x.worker.state==='running'?'يعمل':'متوقف';document.getElementById('workerMeta').textContent=x.worker.description;document.getElementById('sessionState').textContent=x.session.state==='ready'?'جاهزة':x.session.state;document.getElementById('sessionMeta').textContent=x.session.source_configured?'مصدر الجلسة مضبوط':'مصدر الجلسة غير مضبوط';document.getElementById('storageState').textContent=x.render.storage_writable?'متاح':'مشكلة';document.getElementById('storageMeta').textContent=x.render.storage_writable?'حفظ الحالة يعمل':'تحقق من مساحة Render';document.getElementById('activeCount').textContent=active;document.getElementById('totalCount').textContent='إجمالي: '+x.queue.total}if(j.ok)renderJobs(await j.json());document.getElementById('lastUpdate').textContent='آخر تحديث: '+new Date().toLocaleTimeString()}
setInterval(loadAll,3000);loadAll();
</script></body></html>"""
    response = make_response(html.replace("__PHONE__", phone))
    return response


@app.get("/api/jobs")
def jobs_status():
    if not authorized():
        return json_error("غير مصرح", 401)
    return jsonify(read_jobs())


def mutate_job(job_id: int, action: str) -> dict:
    payload = read_jobs()
    jobs = payload.setdefault("jobs", {})
    job = jobs.get(str(job_id))
    if not job:
        raise RuntimeError("العملية غير موجودة")
    current = job.get("status")
    transitions = {
        "fetch": ({"ready"}, "queued", "في قائمة الانتظار للجلب"),
        "cancel": ({"ready", "queued", "processing", "downloading", "uploading", "paused"}, "cancelled", "تم الإلغاء من لوحة Render"),
        "retry": ({"failed", "cancelled"}, "queued", "أعيد إلى قائمة الانتظار من لوحة Render"),
    }
    allowed, status, phase = transitions[action]
    if current not in allowed:
        raise RuntimeError(f"لا يمكن تنفيذ الإجراء على عملية حالتها {current}")
    now = int(__import__("time").time())
    job.update(status=status, phase=phase, updated_at=now, last_activity=now)
    if action in {"fetch", "retry"}:
        job.update(progress=0, current=0, total=0, speed=0, error="", error_message="", error_solution="")
    job.setdefault("events", []).append(f"{now}: {phase}")
    job["events"] = job["events"][-30:]
    job["last_event"] = phase
    path = DATA_DIR / "jobs.json"
    temporary = path.with_suffix(".json.tmp")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return job


@app.post("/api/jobs/<int:job_id>/<action>")
def job_action(job_id: int, action: str):
    if not authorized():
        return json_error("غير مصرح", 401)
    if action not in {"fetch", "cancel", "retry"}:
        return json_error("إجراء غير معروف", 404)
    try:
        return jsonify({"ok": True, "job": mutate_job(job_id, action)})
    except RuntimeError as exc:
        return json_error(str(exc))
    except OSError as exc:
        return json_error(f"تعذر حفظ العملية: {exc}", 500)


@app.get("/api/status")
def service_status():
    if not authorized():
        return json_error("غير مصرح", 401)
    return jsonify(service_snapshot())


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
    global _bot_process, _bot_started_at
    if not authorized():
        return json_error("غير مصرح", 401)
    with _bot_lock:
        if _bot_process is not None and _bot_process.poll() is None:
            return jsonify({"ok": True, "message": "البوت يعمل حاليًا"})
        has_session = bool(env_first("TELEGRAM_SESSION_STRING", "SESSION_STRING", "TMD_SESSION_STRING")) or bool(env_first("TMD_SESSION_B64")) or SESSION_FILE.exists()
        if not has_session:
            return json_error("أكمل تسجيل جلسة Telegram أو رحّلها أولًا")
        setup.close()
        _bot_process = subprocess.Popen([sys.executable, "-m", "Unlock"], cwd=str(Path(__file__).parent))
        _bot_started_at = time.time()
    return jsonify({"ok": True, "message": "تم تشغيل البوت"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    if env_first("TELEGRAM_SESSION_STRING", "SESSION_STRING", "TMD_SESSION_B64") or SESSION_FILE.exists():
        _bot_process = subprocess.Popen([sys.executable, "-m", "Unlock"], cwd=str(Path(__file__).parent))
        _bot_started_at = time.time()
    app.run(host="0.0.0.0", port=port, threaded=True)
