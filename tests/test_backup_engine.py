from pathlib import Path
from unittest.mock import MagicMock

import app.backup_engine as backup_engine
from app.backup_engine import BackupResult, sanitize_name, dir_size_bytes


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


def test_backup_volume_to_file_stages_under_backups_dir(tmp_path: Path, monkeypatch):
    from app.config import BACKUPS_DIR

    captured = {}

    def fake_run(image, command=None, volumes=None, remove=None):
        # Simulate what the real helper container would do: write the archive
        # into whichever host path was bind-mounted at /backup.
        captured["volumes"] = volumes
        host_backup_path = next(k for k, v in volumes.items() if v["bind"] == "/backup")
        (Path(host_backup_path) / "archive.tar.gz").write_bytes(b"fake-tar-data")

    client = MagicMock()
    client.containers.run.side_effect = fake_run
    monkeypatch.setattr(backup_engine, "get_client", lambda: client)

    dest = tmp_path / "vol.tar.gz"
    backup_engine.backup_volume_to_file("some-volume", dest)

    assert dest.read_bytes() == b"fake-tar-data"
    host_backup_path = next(k for k, v in captured["volumes"].items() if v["bind"] == "/backup")
    # Must be under BACKUPS_DIR (bind-mounted from the host) so the Docker
    # daemon and this container resolve the same real directory - not under
    # the system temp dir, which only exists inside this container.
    assert Path(host_backup_path).resolve().is_relative_to(BACKUPS_DIR.resolve())


def test_backup_container_removes_partial_dir_on_failure(tmp_path: Path, monkeypatch):
    container = MagicMock()
    container.name = "failing-app"
    container.attrs = {"Mounts": [], "NetworkSettings": {"Networks": {}}, "Config": {"Image": "x"}}
    container.image.save.side_effect = RuntimeError("docker daemon went away mid-save")

    client = MagicMock()
    client.containers.get.return_value = container
    monkeypatch.setattr(backup_engine, "get_client", lambda: client)

    result = backup_engine.backup_container("failing-app", dest_root=tmp_path)

    assert result.ok is False
    assert not result.path.exists()  # partial data must not linger and inflate disk usage


def test_backup_landscape_carries_member_results_for_tracking(tmp_path: Path, monkeypatch):
    container_a = MagicMock(name="a")
    container_a.name = "app-a"
    container_b = MagicMock(name="b")
    container_b.name = "app-b"
    monkeypatch.setattr(backup_engine, "list_landscape_containers", lambda project_filter=None: [container_a, container_b])

    canned_results = {
        "app-a": BackupResult(ok=True, name="app-a", path=tmp_path / "app-a" / "v1", size_bytes=123),
        "app-b": BackupResult(ok=False, name="app-b", path=tmp_path / "app-b" / "v1", error="boom"),
    }
    monkeypatch.setattr(backup_engine, "backup_container", lambda name, dest_root: canned_results[name])

    result = backup_engine.backup_landscape(dest_root=tmp_path)

    assert [m.name for m in result.member_results] == ["app-a", "app-b"]
    assert result.member_results[0].ok is True
    assert result.member_results[0].size_bytes == 123
    assert result.member_results[1].ok is False
    assert result.member_results[1].error == "boom"
    assert result.ok is False  # one member failed
    assert "app-b: boom" in result.error
