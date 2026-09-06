from Unlock.modules.progress import format_bytes, progress_bar, transfer_text


def test_format_bytes_uses_readable_units():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(5 * 1024**3) == "5.0 GB"


def test_progress_bar_clamps_percentage():
    assert progress_bar(50, 100, width=10) == "[■■■■■□□□□□] 50%"
    assert progress_bar(150, 100, width=10) == "[■■■■■■■■■■] 150%"


def test_transfer_text_includes_size_and_speed():
    text = transfer_text("جاري الإرسال", 5 * 1024**2, 10 * 1024**2, 1024**2)
    assert "50%" in text
    assert "5.0 MB / 10.0 MB" in text
    assert "1.0 MB/ث" in text
