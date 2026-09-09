from __future__ import annotations

import base64
import importlib
import os
import sys
import tempfile
from pathlib import Path


def load_start_module(data_dir: Path, encoded: str, mode: str = "preserve"):
    os.environ["TMD_SUITE_DATA"] = str(data_dir)
    os.environ["TMD_SESSION_B64"] = encoded
    os.environ["TMD_SESSION_IMPORT_MODE"] = mode
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    module = importlib.import_module("render_start")
    module.DATA_DIR = data_dir
    module.SESSION_DIR = data_dir / "sessions"
    module.STORAGE_DIR = data_dir / "storage"
    module.SETTINGS_FILE = data_dir / "settings.json"
    return module


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        data_dir = Path(raw)
        old = b"old-session"
        new = b"new-session"
        encoded_new = base64.b64encode(new).decode("ascii")
        module = load_start_module(data_dir, encoded_new)
        module.SESSION_DIR.mkdir(parents=True)
        session_file = module.SESSION_DIR / "tmd_user.session"
        session_file.write_bytes(old)

        module.bootstrap()
        assert session_file.read_bytes() == old, "preserve mode replaced an existing session"

        os.environ["TMD_SESSION_IMPORT_MODE"] = "replace"
        module.bootstrap()
        assert session_file.read_bytes() == new, "replace mode did not import the requested session"

    print("session management checks passed")


if __name__ == "__main__":
    main()
