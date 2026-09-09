from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable


class RenderSessionSync:
    """Push a newly-created Telegram session to one Render service."""

    def __init__(self, log: Callable[[str], None] | None = None):
        self.log = log or (lambda message: None)

    def enabled(self) -> bool:
        return bool(os.environ.get("RENDER_API_KEY", "").strip() and
                    os.environ.get("RENDER_SERVICE_ID", "").strip())

    def push_file(self, session_file: Path) -> bool:
        api_key = os.environ.get("RENDER_API_KEY", "").strip()
        service_id = os.environ.get("RENDER_SERVICE_ID", "").strip()
        if not api_key or not service_id:
            self.log("مزامنة جلسة Render غير مفعلة؛ أضف RENDER_API_KEY وRENDER_SERVICE_ID.")
            return False
        if not session_file.exists():
            self.log("تعذر مزامنة الجلسة مع Render: ملف الجلسة غير موجود.")
            return False
        try:
            encoded = base64.b64encode(session_file.read_bytes()).decode("ascii")
            self._put_value(api_key, service_id, encoded)
            self.log("تم تحديث نسخة الجلسة في Render بنجاح دون تسجيل محتواها.")
            return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            self.log(f"تعذر تحديث نسخة الجلسة في Render: {type(exc).__name__}")
            return False

    def clear(self) -> bool:
        """Clear the remote copy before an explicit local session reset."""
        api_key = os.environ.get("RENDER_API_KEY", "").strip()
        service_id = os.environ.get("RENDER_SERVICE_ID", "").strip()
        if not api_key or not service_id:
            return True
        try:
            self._put_value(api_key, service_id, "")
            self.log("تمت إزالة نسخة الجلسة القديمة من Render.")
            return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            self.log(f"تعذر إزالة نسخة الجلسة من Render: {type(exc).__name__}")
            return False

    @staticmethod
    def _put_value(api_key: str, service_id: str, value: str) -> None:
        url = f"https://api.render.com/v1/services/{service_id}/env-vars/TMD_SESSION_B64"
        request = urllib.request.Request(
            url,
            data=json.dumps({"value": value}).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Render API HTTP {response.status}")
