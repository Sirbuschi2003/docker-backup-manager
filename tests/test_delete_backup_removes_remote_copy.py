import json

from fastapi.testclient import TestClient


def test_deleting_a_backup_also_removes_it_from_synced_storage_targets(tmp_path):
    from app.config import BACKUPS_DIR
    from app.database import SessionLocal
    from app.main import app
    from app.models import BackupRecord, StorageTarget
    from app.reset_password import reset_password
    from app.storage_sync import _relative_key, sync_local_path

    reset_password("delete-backup-user", "supersecret1")

    backup_dir = BACKUPS_DIR / "app-del" / "v1"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "f.txt").write_text("data")

    remote_root = tmp_path / "remote"
    sync_local_path(backup_dir, {"path": str(remote_root)})
    relative_key = _relative_key(backup_dir)
    remote_copy = remote_root / relative_key
    assert remote_copy.exists()

    db = SessionLocal()
    try:
        target = StorageTarget(name="local-test-target", type="local_path",
                                config_json=json.dumps({"path": str(remote_root)}), enabled=True)
        db.add(target)
        db.commit()
        db.refresh(target)

        record = BackupRecord(
            backup_type="container", name="app-del", path=str(backup_dir), status="ok",
            synced_target_ids=json.dumps([target.id]),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        record_id = record.id
    finally:
        db.close()

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "delete-backup-user", "password": "supersecret1"})
        resp = client.delete(f"/api/backups/{record_id}")
        assert resp.status_code == 200
        assert "warning" not in resp.json()

    assert not remote_copy.exists()
    assert not backup_dir.exists()
