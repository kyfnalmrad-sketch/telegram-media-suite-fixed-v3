from __future__ import annotations

import base64
import importlib.util
import os
import tempfile
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[0] / "render_start.py"
spec = importlib.util.spec_from_file_location("render_start_test", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as temp:
    data_dir = Path(temp) / "data"
    module.DATA_DIR = data_dir
    module.SESSION_DIR = data_dir / "sessions"
    module.STORAGE_DIR = data_dir / "storage"
    module.SETTINGS_FILE = data_dir / "settings.json"
    session_file = module.SESSION_DIR / "tmd_user.session"
    original = b"newer-session"
    stale = base64.b64encode(b"stale-session").decode()
    os.environ["TMD_SESSION_B64"] = stale
    os.environ.pop("TMD_SESSION_B64_FORCE", None)
    module.bootstrap()
    session_file.write_bytes(original)
    module.bootstrap()
    assert session_file.read_bytes() == original
    os.environ["TMD_SESSION_B64_FORCE"] = "true"
    module.bootstrap()
    assert session_file.read_bytes() == b"stale-session"
    os.environ.pop("TMD_SESSION_B64", None)
    os.environ.pop("TMD_SESSION_B64_FORCE", None)

print("RENDER_SESSION_PERSISTENCE_OK")
