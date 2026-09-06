from __future__ import annotations

from pathlib import Path


def resolve_upload_path(result: dict) -> Path:
    """Return a verified local file path for the upload stage.

    Pyrogram treats a non-existent string as a file id and raises a misleading
    decode error. Resolving and validating the path here lets callers report the
    real problem before starting an upload.
    """
    raw_path = result.get("path")
    if not raw_path:
        raise FileNotFoundError("Telegram لم يرجع مسار ملف صالح للتسليم")
    path = Path(str(raw_path)).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError(f"ملف الوسائط غير موجود: {path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"مسار الوسائط ليس ملفًا: {path}")
    return path


__all__ = ["resolve_upload_path"]


if __name__ == "__main__":
    raise SystemExit("This module is imported by the bot and is not a standalone command.")

