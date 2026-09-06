from pathlib import Path

import pytest

from Unlock.modules.media_upload import resolve_upload_path


def test_resolve_upload_path_returns_existing_file(tmp_path: Path):
    media = tmp_path / "media.bin"
    media.write_bytes(b"telegram-media")

    resolved = resolve_upload_path({"path": str(media)})

    assert resolved == media.resolve()
    assert resolved.is_file()


def test_resolve_upload_path_rejects_missing_file(tmp_path: Path):
    missing = tmp_path / "missing.bin"

    with pytest.raises(FileNotFoundError, match="ملف الوسائط غير موجود"):
        resolve_upload_path({"path": str(missing)})


def test_resolve_upload_path_rejects_empty_result():
    with pytest.raises(FileNotFoundError, match="مسار ملف صالح"):
        resolve_upload_path({})

