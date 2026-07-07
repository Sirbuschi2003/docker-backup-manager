import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import encryption, storage_sync
from app.auth import get_current_user
from app.config import BACKUPS_DIR, DEFAULT_RETENTION_COUNT, DEFAULT_RETENTION_DAYS
from app.database import get_db
from app.docker_client import is_available
from app.models import StorageTarget, User

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/overview")
def overview(user: User = Depends(get_current_user)):
    docker_ok, docker_error = is_available()
    total_size = 0
    if BACKUPS_DIR.exists():
        total_size = sum(f.stat().st_size for f in BACKUPS_DIR.rglob("*") if f.is_file())
    return {
        "backups_dir": str(BACKUPS_DIR),
        "backups_total_bytes": total_size,
        "docker_available": docker_ok,
        "docker_error": docker_error,
        "default_retention_count": DEFAULT_RETENTION_COUNT,
        "default_retention_days": DEFAULT_RETENTION_DAYS,
        "encryption_enabled": encryption.is_enabled(),
    }


class StorageTargetPayload(BaseModel):
    name: str
    type: str  # "local_path" | "s3" | "rclone"
    config: dict
    enabled: bool = True


@router.get("/storage-targets")
def list_targets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(StorageTarget).order_by(StorageTarget.created_at.desc()).all()
    return {"targets": [
        {
            "id": t.id, "name": t.name, "type": t.type, "config": json.loads(t.config_json),
            "enabled": t.enabled, "last_sync_at": t.last_sync_at.isoformat() + "Z" if t.last_sync_at else None,
            "last_sync_status": t.last_sync_status, "last_sync_error": t.last_sync_error,
        } for t in rows
    ]}


@router.post("/storage-targets")
def create_target(payload: StorageTargetPayload, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    if payload.type not in ("local_path", "smb", "s3", "rclone"):
        raise HTTPException(400, "Invalid target type")
    target = StorageTarget(
        name=payload.name, type=payload.type, config_json=json.dumps(payload.config), enabled=payload.enabled,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return {"id": target.id}


@router.put("/storage-targets/{target_id}")
def update_target(target_id: int, payload: StorageTargetPayload, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    target = db.query(StorageTarget).filter(StorageTarget.id == target_id).first()
    if not target:
        raise HTTPException(404, "Storage target not found")
    target.name = payload.name
    target.type = payload.type
    target.config_json = json.dumps(payload.config)
    target.enabled = payload.enabled
    db.commit()
    return {"ok": True}


@router.delete("/storage-targets/{target_id}")
def delete_target(target_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target = db.query(StorageTarget).filter(StorageTarget.id == target_id).first()
    if not target:
        raise HTTPException(404, "Storage target not found")
    db.delete(target)
    db.commit()
    return {"ok": True}


class StorageTargetTestPayload(BaseModel):
    type: str
    config: dict


@router.post("/storage-targets/test")
def test_storage_target_config(payload: StorageTargetTestPayload, user: User = Depends(get_current_user)):
    """Tests connection settings before a target has been saved, so mistakes
    (wrong share name, bad credentials, ...) surface immediately in the dialog."""
    try:
        storage_sync.check_target_connection(payload.type, json.dumps(payload.config))
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Connection test failed: {exc}")


class SmbSharesPayload(BaseModel):
    server: str
    username: str
    password: str
    domain: str = ""
    port: str = "445"


@router.post("/smb/shares")
def list_smb_shares(payload: SmbSharesPayload, user: User = Depends(get_current_user)):
    try:
        shares = storage_sync.list_smb_shares(payload.model_dump())
        return {"shares": shares}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Freigaben konnten nicht abgerufen werden: {exc}")


@router.post("/storage-targets/{target_id}/test")
def test_storage_target(target_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target = db.query(StorageTarget).filter(StorageTarget.id == target_id).first()
    if not target:
        raise HTTPException(404, "Storage target not found")
    try:
        storage_sync.check_target_connection(target.type, target.config_json)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Connection test failed: {exc}")
