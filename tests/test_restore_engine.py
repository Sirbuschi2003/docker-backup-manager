import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.restore_engine import _build_create_kwargs, restore_container


def test_build_create_kwargs_basic_fields():
    container_json = {
        "Name": "/my-app",
        "Config": {
            "Cmd": ["python", "app.py"],
            "Entrypoint": None,
            "Env": ["FOO=bar"],
            "Labels": {"com.example": "1"},
            "WorkingDir": "/app",
            "Hostname": "myhost",
            "User": "",
        },
        "HostConfig": {
            "PortBindings": {"8000/tcp": [{"HostIp": "", "HostPort": "8080"}]},
            "RestartPolicy": {"Name": "unless-stopped"},
            "Privileged": False,
            "CapAdd": None,
            "CapDrop": None,
            "NetworkMode": "bridge",
        },
        "Mounts": [
            {"Type": "volume", "Name": "data-vol", "Destination": "/data", "RW": True},
            {"Type": "bind", "Source": "/host/path", "Destination": "/bind", "RW": True},
        ],
    }
    kwargs = _build_create_kwargs(container_json, new_name=None, image_ref="myimage:latest")

    assert kwargs["image"] == "myimage:latest"
    assert kwargs["name"] == "my-app"
    assert kwargs["command"] == ["python", "app.py"]
    assert kwargs["environment"] == ["FOO=bar"]
    assert kwargs["ports"] == {"8000/tcp": "8080"}
    assert kwargs["volumes"]["data-vol"] == {"bind": "/data", "mode": "rw"}
    assert kwargs["volumes"]["/host/path"] == {"bind": "/bind", "mode": "rw"}
    assert kwargs["restart_policy"] == {"Name": "unless-stopped"}


def test_build_create_kwargs_new_name_overrides():
    container_json = {"Name": "/old-name", "Config": {}, "HostConfig": {}, "Mounts": []}
    kwargs = _build_create_kwargs(container_json, new_name="new-name", image_ref="img")
    assert kwargs["name"] == "new-name"


def test_build_create_kwargs_no_restart_policy_when_empty():
    container_json = {
        "Name": "/x", "Config": {}, "Mounts": [],
        "HostConfig": {"RestartPolicy": {"Name": ""}},
    }
    kwargs = _build_create_kwargs(container_json, None, "img")
    assert "restart_policy" not in kwargs


def test_restore_container_downloads_streamed_volumes_before_restoring(tmp_path, monkeypatch):
    import app.restore_engine as restore_engine

    backup_dir = tmp_path / "backups" / "app-streamed" / "v1"
    backup_dir.mkdir(parents=True)
    (backup_dir / "container.json").write_text(json.dumps({"Name": "/app", "Config": {}, "HostConfig": {}}))
    (backup_dir / "image.tar").write_bytes(b"fake-image-tar")
    (backup_dir / "meta.json").write_text(json.dumps({
        "volumes": ["data-vol"], "streamed_target_id": 1,
    }))

    # Volume lives only on the streamed target, uploaded there directly.
    remote_root = tmp_path / "remote"
    from app.storage_sync import stream_upload_to_target
    relative_key = "app-streamed/v1"
    stream_upload_to_target(
        "local_path", json.dumps({"path": str(remote_root)}), f"{relative_key}/volumes/data-vol.tar.gz",
        iter([b"volume-payload"]),
    )
    monkeypatch.setattr(restore_engine.storage_sync, "_relative_key", lambda p: relative_key)

    client = MagicMock()
    loaded_image = MagicMock()
    loaded_image.tags = ["app:latest"]
    client.images.load.return_value = [loaded_image]
    client.networks.list.return_value = []
    client.volumes.list.return_value = []
    monkeypatch.setattr(restore_engine, "get_client", lambda: client)

    restored_volumes = {}

    def fake_restore_volume_from_file(vol_name, vol_file):
        restored_volumes[vol_name] = Path(vol_file).read_bytes()

    monkeypatch.setattr(restore_engine, "restore_volume_from_file", fake_restore_volume_from_file)

    stream_target = ("local_path", json.dumps({"path": str(remote_root)}), 1)
    restore_container(backup_dir, stream_target=stream_target)

    assert restored_volumes == {"data-vol": b"volume-payload"}
    client.volumes.create.assert_called_once_with(name="data-vol")


def test_restore_container_raises_clearly_if_streamed_target_missing(tmp_path, monkeypatch):
    import app.restore_engine as restore_engine

    backup_dir = tmp_path / "backups" / "app-streamed" / "v1"
    backup_dir.mkdir(parents=True)
    (backup_dir / "container.json").write_text(json.dumps({"Name": "/app", "Config": {}, "HostConfig": {}}))
    (backup_dir / "image.tar").write_bytes(b"fake-image-tar")
    (backup_dir / "meta.json").write_text(json.dumps({"volumes": ["data-vol"], "streamed_target_id": 1}))

    monkeypatch.setattr(restore_engine, "get_client", lambda: MagicMock())

    with pytest.raises(RuntimeError):
        restore_container(backup_dir, stream_target=None)


def test_restore_container_bulk_downloads_a_catalog_imported_backup(tmp_path, monkeypatch):
    """Simulates the "fresh host, only the catalog import ran" scenario: the
    local backup directory doesn't exist at all yet, only a remote copy on
    the target does - restore must pull the whole thing down first."""
    import app.restore_engine as restore_engine

    relative_key = "app-imported/20260101T030000Z"
    remote_root = tmp_path / "remote"
    remote_backup = remote_root / relative_key
    (remote_backup / "volumes").mkdir(parents=True)
    (remote_backup / "container.json").write_text(json.dumps({"Name": "/app", "Config": {}, "HostConfig": {}}))
    (remote_backup / "image.tar").write_bytes(b"fake-image-tar")
    (remote_backup / "meta.json").write_text(json.dumps({"volumes": ["data-vol"]}))
    (remote_backup / "volumes" / "data-vol.tar.gz").write_bytes(b"volume-payload")

    # This backup was never local - matches what list_backups_on_target()/the
    # import-catalog endpoint would have set record.path to.
    backup_dir = tmp_path / "backups" / relative_key
    assert not backup_dir.exists()
    monkeypatch.setattr(restore_engine.storage_sync, "_relative_key", lambda p: relative_key)

    client = MagicMock()
    loaded_image = MagicMock()
    loaded_image.tags = ["app:latest"]
    client.images.load.return_value = [loaded_image]
    client.networks.list.return_value = []
    client.volumes.list.return_value = []
    monkeypatch.setattr(restore_engine, "get_client", lambda: client)

    restored_volumes = {}
    monkeypatch.setattr(restore_engine, "restore_volume_from_file",
                         lambda vol_name, vol_file: restored_volumes.__setitem__(vol_name, Path(vol_file).read_bytes()))

    stream_target = ("local_path", json.dumps({"path": str(remote_root)}), 1)
    restore_container(backup_dir, stream_target=stream_target)

    assert backup_dir.exists()  # materialized locally as a side effect of restoring
    assert (backup_dir / "meta.json").exists()
    assert restored_volumes == {"data-vol": b"volume-payload"}
    client.volumes.create.assert_called_once_with(name="data-vol")


def test_restore_container_raises_clearly_if_missing_locally_and_no_target(tmp_path, monkeypatch):
    import app.restore_engine as restore_engine

    backup_dir = tmp_path / "backups" / "never-existed" / "v1"
    monkeypatch.setattr(restore_engine, "get_client", lambda: MagicMock())

    with pytest.raises(RuntimeError):
        restore_container(backup_dir, stream_target=None)
