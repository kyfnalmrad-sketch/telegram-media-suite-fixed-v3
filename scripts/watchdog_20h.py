from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("TMD_SUITE_DATA", str(Path.home() / "tmd-live-server")))
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
WATCHDOG_LOG = LOG_DIR / "watchdog_20h.log"
HEALTH_URL = "http://127.0.0.1:8765/api/state"
CHECK_SECONDS = 5 * 60
MAX_SECONDS = 20 * 60 * 60


def write_log(text: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} — {text}\n"
    with WATCHDOG_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line)


def start_server() -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["TMD_SUITE_DATA"] = str(DATA_DIR)
    env["TMD_HOST"] = "127.0.0.1"
    server_log = (LOG_DIR / "web_app_20h.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(PROJECT / "app" / "web_app.py")],
        cwd=str(PROJECT),
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    write_log(f"تم تشغيل الخادم، PID={process.pid}")
    return process


def visit_health() -> bool:
    request = Request(HEALTH_URL, headers={"User-Agent": "TelegramMediaSuite-Watchdog/3.2.0"})
    try:
        with urlopen(request, timeout=20) as response:
            ok = 200 <= response.status < 300
            write_log(f"فحص محلي: {'سليم' if ok else 'استجابة غير سليمة'}، HTTP={response.status}")
            return ok
    except Exception as exc:
        write_log(f"فشل الفحص المحلي: {type(exc).__name__}")
        return False


def stop_server(process: subprocess.Popen[str] | None) -> None:
    if not process or process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=15)
    except Exception:
        process.kill()
    write_log("تم إيقاف الخادم بانتهاء نافذة التشغيل")


def main() -> None:
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    write_log("بدأ مراقب ٢٠ ساعة؛ الفحص المحلي كل ٥ دقائق")
    process: subprocess.Popen[str] | None = None
    try:
        process = start_server()
        time.sleep(8)
        visit_health()
        while time.monotonic() < deadline:
            if process.poll() is not None:
                write_log("الخادم توقف؛ إعادة التشغيل تلقائيًا")
                process = start_server()
                time.sleep(8)
            if not visit_health():
                if process.poll() is None:
                    write_log("الخادم موجود لكن الفحص فشل؛ إعادة تشغيل احتياطية")
                    stop_server(process)
                process = start_server()
                time.sleep(8)
                visit_health()
            remaining = max(0.0, deadline - time.monotonic())
            time.sleep(min(CHECK_SECONDS, remaining))
    finally:
        stop_server(process)
        write_log("انتهت نافذة مراقب ٢٠ ساعة")


if __name__ == "__main__":
    main()
