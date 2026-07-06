import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import backup_engine, job_tracker, storage_sync
from app.auth import get_current_user
from app.config import BACKUPS_DIR
from app.database import SessionLocal, get_db
from app.models import BackupRecord, User
from app.restore_engine import restore_container

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("")
def list_backups(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    records = db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).all()
    grouped: dict[str, list] = {}
    for r in records:
        grouped.setdefault(r.name, []).append({
            "id": r.id,
            "backup_type": r.backup_type,
            "status": r.status,
            "error": r.error,
            "size_bytes": r.size_bytes,
            "created_at": r.created_at.isoformat() + "Z",
            "containers": json.loads(r.containers_json) if r.containers_json else [],
        })
    return {"groups": grouped}


@router.delete("/{backup_id}")
def delete_backup(backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    record = db.query(BackupRecord).filter(BackupRecord.id == backup_id).first()
    if not record:
        raise HTTPException(404, "Backup not found")
    backup_engine.delete_backup(Path(record.path))
    db.delete(record)
    db.commit()
    return {"ok": True}


class LandscapeBackupPayload(BaseModel):
    label: Optional[str] = None
    project_filter: Optional[str] = None


def _run_landscape_job(job_id: str, label: Optional[str], project_filter: Optional[str]):
    db = SessionLocal()
    try:
        def progress(step, name, total=None):
            job_tracker.update_progress(job_id, step, name, total)

        result = backup_engine.backup_landscape(BACKUPS_DIR, project_filter=project_filter,
                                                  label=label, on_progress=progress)
        record = BackupRecord(
            backup_type="landscape",
            name=result.name,
            path=str(result.path),
            status="ok" if result.ok else "failed",
            error=result.error,
            size_bytes=result.size_bytes,
            containers_json=json.dumps(result.containers),
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        if result.ok:
            def upload_progress(label_, idx, total):
                job_tracker.update_progress(job_id, 1, label_, 1)

            storage_sync.sync_to_all_targets(result.path, on_progress=upload_progress)

        job_tracker.finish_job(job_id, result.ok, result.error, record.id)
    except Exception as exc:  # noqa: BLE001
        job_tracker.finish_job(job_id, False, str(exc))
    finally:
        db.close()


@router.post("/landscape")
def backup_landscape_now(payload: LandscapeBackupPayload, user: User = Depends(get_current_user)):
    job = job_tracker.create_job("backup", payload.label or "landscape", total_steps=1)
    thread = threading.Thread(
        target=_run_landscape_job, args=(job.id, payload.label, payload.project_filter), daemon=True,
    )
    thread.start()
    return {"job_id": job.id}


class RestorePayload(BaseModel):
    new_name: Optional[str] = None
    start: bool = True


def _run_restore_job(job_id: str, backup_path: str, new_name: Optional[str], start: bool):
    try:
        def progress(step, name, total=None):
            job_tracker.update_progress(job_id, step, name, total)

        restore_container(Path(backup_path), new_name=new_name, start=start, on_progress=progress)
        job_tracker.finish_job(job_id, True)
    except Exception as exc:  # noqa: BLE001
        job_tracker.finish_job(job_id, False, str(exc))


@router.post("/{backup_id}/restore")
def restore_backup(backup_id: int, payload: RestorePayload, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    record = db.query(BackupRecord).filter(BackupRecord.id == backup_id).first()
    if not record:
        raise HTTPException(404, "Backup not found")
    if record.backup_type != "container":
        raise HTTPException(400, "Only single-container backups can be restored directly; "
                                  "restore each member container of a landscape backup individually")

    job = job_tracker.create_job("restore", record.name, total_steps=1)
    thread = threading.Thread(
        target=_run_restore_job, args=(job.id, record.path, payload.new_name, payload.start), daemon=True,
    )
    thread.start()
    return {"job_id": job.id}


@router.get("/{backup_id}/members")
def landscape_members(backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """For a landscape backup, resolve each member container name to its own
    (matching, same-run) container BackupRecord id so the UI can offer restore
    per member."""
    record = db.query(BackupRecord).filter(BackupRecord.id == backup_id).first()
    if not record or record.backup_type != "landscape":
        raise HTTPException(404, "Landscape backup not found")

    members = json.loads(record.containers_json) if record.containers_json else []
    result = []
    for member_name in members:
        candidate = (
            db.query(BackupRecord)
            .filter(BackupRecord.name == member_name, BackupRecord.backup_type == "container")
            .order_by(BackupRecord.created_at.desc())
            .first()
        )
        result.append({"container_name": member_name, "backup_id": candidate.id if candidate else None})
    return {"members": result}
