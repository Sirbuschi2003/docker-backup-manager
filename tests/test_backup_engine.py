from pathlib import Path

from app.backup_engine import sanitize_name, dir_size_bytes


def test_sanitize_name_keeps_safe_chars():
    assert sanitize_name("my-app_1.0") == "my-app_1.0"


def test_sanitize_name_strips_unsafe_chars():
    assert sanitize_name("my app/../weird:name") == "my_app_.._weird_name"


def test_dir_size_bytes(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello")  # 5 bytes
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world!")  # 6 bytes
    assert dir_size_bytes(tmp_path) == 11
