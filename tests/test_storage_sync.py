import json
from pathlib import Path

from app.config import BACKUPS_DIR
from app.storage_sync import sync_local_path, check_target_connection


def test_sync_local_path_copies_tree(tmp_path: Path):
    backup_dir = BACKUPS_DIR / "app1" / "20240101T000000Z"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "meta.json").write_text('{"ok": true}')

    dest_root = tmp_path / "remote"
    sync_local_path(backup_dir, {"path": str(dest_root)})

    copied = dest_root / "app1" / "20240101T000000Z" / "meta.json"
    assert copied.exists()
    assert json.loads(copied.read_text()) == {"ok": True}


def test_sync_local_path_overwrites_existing_version(tmp_path: Path):
    backup_dir = BACKUPS_DIR / "app2" / "v1"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "f.txt").write_text("new")

    dest_root = tmp_path / "remote2"
    old_dest = dest_root / "app2" / "v1"
    old_dest.mkdir(parents=True)
    (old_dest / "f.txt").write_text("old")
    (old_dest / "stale.txt").write_text("stale")

    sync_local_path(backup_dir, {"path": str(dest_root)})

    assert (old_dest / "f.txt").read_text() == "new"
    assert not (old_dest / "stale.txt").exists()


def test_check_target_connection_local_path_write_check(tmp_path: Path):
    target_dir = tmp_path / "share"
    check_target_connection("local_path", json.dumps({"path": str(target_dir)}))
    assert target_dir.exists()
    assert not (target_dir / ".dbm_write_test").exists()
