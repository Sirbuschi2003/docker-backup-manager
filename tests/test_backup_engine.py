import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


def _fake_helper_container(chunks, status_code=0):
    """Builds a MagicMock container as returned by client.containers.run(detach=True):
    .logs(stream=True) yields the given chunks, .wait() reports status_code."""
    container = MagicMock()
    container.logs.return_value = iter(chunks)
    container.wait.return_value = {"StatusCode": status_code}
    return container


def test_iter_volume_tar_chunks_yields_container_stdout(monkeypatch):
    container = _fake_helper_container([b"chunk1", b"chunk2"])
    client = MagicMock()
    client.containers.run.return_value = container
    monkeypatch.setattr(backup_engine, "get_client", lambda: client)

    chunks = list(backup_engine.iter_volume_tar_chunks("some-volume"))

    assert chunks == [b"chunk1", b"chunk2"]
    # No bind mount at all - just the read-only volume, avoiding the host-path
    # resolution problem entirely (see module docstring on iter_volume_tar_chunks).
    run_kwargs = client.containers.run.call_args.kwargs
    assert run_kwargs["volumes"] == {"some-volume": {"bind": "/data", "mode": "ro"}}
    container.remove.assert_called_once_with(force=True)


def test_iter_volume_tar_chunks_raises_on_nonzero_exit(monkeypatch):
    container = _fake_helper_container([b"partial"], status_code=1)
    client = MagicMock()
    client.containers.run.return_value = container
    monkeypatch.setattr(backup_engine, "get_client", lambda: client)

    with pytest.raises(RuntimeError):
        list(backup_engine.iter_volume_tar_chunks("some-volume"))
    container.remove.assert_called_once_with(force=True)


def test_backup_volume_to_file_writes_streamed_chunks(tmp_path: Path, monkeypatch):
    container = _fake_helper_container([b"fake-", b"tar-data"])
    client = MagicMock()
    client.containers.run.return_value = container
    monkeypatch.setattr(backup_engine, "get_client", lambda: client)

    dest = tmp_path / "vol.tar.gz"
    backup_engine.backup_volume_to_file("some-volume", dest)

    assert dest.read_bytes() == b"fake-tar-data"


def test_stream_volume_to_target_uploads_without_local_file(tmp_path: Path, monkeypatch):
    container = _fake_helper_container([b"fake-", b"tar-data"])
    client = MagicMock()
    client.containers.run.return_value = container
    monkeypatch.setattr(backup_engine, "get_client", lambda: client)

    dest = tmp_path / "remote" / "vol.tar.gz"
    backup_engine.stream_volume_to_target(
        "some-volume", "local_path", json.dumps({"path": str(tmp_path / "remote")}), "vol.tar.gz",
    )

    assert dest.read_bytes() == b"fake-tar-data"


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


def test_should_backup_bind_mount_skips_docker_internals():
    assert backup_engine._should_backup_bind_mount("/var/run/docker.sock") is False
    assert backup_engine._should_backup_bind_mount("/proc") is False
    assert backup_engine._should_backup_bind_mount("/proc/1/ns") is False
    assert backup_engine._should_backup_bind_mount("/sys/fs/cgroup") is False
    assert backup_engine._should_backup_bind_mount("/some/other.sock") is False


def test_should_backup_bind_mount_allows_real_data_paths():
    assert backup_engine._should_backup_bind_mount("/mnt/bigdisk/nextcloud-data") is True
    assert backup_engine._should_backup_bind_mount("/srv/app-config") is True


def test_backup_container_archives_bind_mounts_and_skips_denylisted_ones(tmp_path: Path, monkeypatch):
    container = MagicMock()
    container.name = "nextcloud"
    container.attrs = {
        "Mounts": [
            {"Type": "volume", "Name": "nc-db"},
            {"Type": "bind", "Source": "/mnt/bigdisk/nextcloud-data", "Destination": "/var/www/html/data", "RW": True},
            {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock", "RW": True},
        ],
        "NetworkSettings": {"Networks": {}},
        "Config": {"Image": "nextcloud:latest"},
    }
    container.image.save.return_value = iter([b"image-bytes"])

    client = MagicMock()
    client.containers.get.return_value = container
    client.version.return_value = {"ApiVersion": "1.45"}

    def fake_helper_run(image, command=None, volumes=None, detach=None):
        # volumes has exactly one key: the volume name or bind source being archived.
        key = next(iter(volumes))
        content = f"data-for-{key}".encode()
        return _fake_helper_container([content])

    client.containers.run.side_effect = fake_helper_run
    monkeypatch.setattr(backup_engine, "get_client", lambda: client)

    result = backup_engine.backup_container("nextcloud", dest_root=tmp_path)

    assert result.ok is True
    meta = json.loads((result.path / "meta.json").read_text())
    assert meta["bind_mounts"] == [{
        "source": "/mnt/bigdisk/nextcloud-data", "destination": "/var/www/html/data",
        "filename": "_var_www_html_data.tar.gz", "rw": True,
    }]
    # The docker.sock bind mount must not show up anywhere - not archived, not in meta.
    assert (result.path / "binds" / "_var_www_html_data.tar.gz").read_bytes() == b"data-for-/mnt/bigdisk/nextcloud-data"
    assert not (result.path / "binds" / "var_run_docker.sock.tar.gz").exists()
    assert (result.path / "volumes" / "nc-db.tar.gz").exists()


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
    monkeypatch.setattr(backup_engine, "backup_container",
                         lambda name, dest_root, stream_target=None: canned_results[name])

    result = backup_engine.backup_landscape(dest_root=tmp_path)

    assert [m.name for m in result.member_results] == ["app-a", "app-b"]
    assert result.member_results[0].ok is True
    assert result.member_results[0].size_bytes == 123
    assert result.member_results[1].ok is False
    assert result.member_results[1].error == "boom"
    assert result.ok is False  # one member failed
    assert "app-b: boom" in result.error
