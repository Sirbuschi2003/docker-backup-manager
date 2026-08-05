import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import backup_engine, event_log, job_tracker, restic_engine, storage_sync
from app.auth import get_current_user
from app.config import BACKUPS_DIR
from app.database import SessionLocal, get_db
from app.models import BackupRecord, StorageTarget, User
from app.restore_engine import restore_container
from app.storage_sync import _relative_key

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("")
def list_backups(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    records = db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).all()

    # Self-heal: a successful backup whose directory no longer exists on disk
    # (e.g. deleted outside the app, or left behind by a retention pass that
    # was interrupted before committing) is just stale, misleading data -
    # drop it rather than showing a version that can never actually be
    # restored or re-deleted. Failed backups intentionally have no directory
    # (the partial data is cleaned up right away), so those are left alone.
    stale = [r for r in records if r.status == "ok" and not Path(r.path).exists()]
    if stale:
        for r in stale:
            db.delete(r)
        db.commit()
        records = [r for r in records if r not in stale]

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


def _cleanup_remote_for_record(record: BackupRecord, db: Session) -> list[str]:
    """Bereinigt alle Remote-Daten für ein einzelnes Backup-Record.
    Gibt eine Liste von Fehlermeldungen zurück (leer = alles ok)."""
    errors = []

    # 1. Synced targets: normale Datei-Löschung
    for target_id in json.loads(record.synced_target_ids or "[]"):
        target = db.query(StorageTarget).filter(StorageTarget.id == target_id).first()
        if not target:
            continue
        try:
            storage_sync.delete_from_target(target.type, target.config_json, _relative_key(Path(record.path)))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{target.name}: {exc}")

    # 2. Stream target (nur Container-Backups haben Restic-Repos)
    if record.streamed_target_id is not None and record.backup_type == "container":
        target = db.query(StorageTarget).filter(StorageTarget.id == record.streamed_target_id).first()
        if target:
            stream_target = (target.type, target.config_json, record.streamed_target_id)
            try:
                # Bekannte Snapshots vergessen
                restic_engine.maybe_forget_restic(Path(record.path), stream_target)

                # Wenn dies der letzte Record für diesen Container auf diesem Ziel ist,
                # den gesamten Repo-Ordner forciert löschen — auch wenn noch orphaned
                # Snapshots aus fehlgeschlagenen Backups vorhanden sind.
                other_count = db.query(BackupRecord).filter(
                    BackupRecord.name == record.name,
                    BackupRecord.streamed_target_id == record.streamed_target_id,
                    BackupRecord.backup_type == "container",
                    BackupRecord.id != record.id,
                ).count()
                if other_count == 0:
                    restic_engine.purge_restic_repo(record.name, stream_target)

                # Für alte Tar-Stream-Backups (kein restic) normal löschen
                meta_path = Path(record.path) / "meta.json"
                try:
                    _meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
                except Exception:
                    _meta = {}
                if _meta.get("backup_engine") != "restic":
                    storage_sync.delete_from_target(
                        target.type, target.config_json, _relative_key(Path(record.path))
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{target.name}: {exc}")

    return errors


@router.delete("/{backup_id}")
def delete_backup(backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    record = db.query(BackupRecord).filter(BackupRecord.id == backup_id).first()
    if not record:
        raise HTTPException(404, "Backup not found")

    remote_errors = []

    # Landscape-Backups: zuerst alle Mitglieder-Container-Records löschen.
    # Die Member-Daten (lokale Verzeichnisse + Restic-Repos) sind in den Member-Records,
    # nicht im Landscape-Record selbst.
    if record.backup_type == "landscape" and Path(record.path).exists():
        for jf in sorted(Path(record.path).glob("*.json")):
            try:
                data = json.loads(jf.read_text())
                member_name = data.get("container_name", "")
                member_path = data.get("backup_path", "")
                if not member_path:
                    continue
                member = (
                    db.query(BackupRecord)
                    .filter(BackupRecord.path == member_path, BackupRecord.name == member_name)
                    .first()
                )
                if not member:
                    continue
                remote_errors.extend(_cleanup_remote_for_record(member, db))
                backup_engine.delete_backup(Path(member.path))
                db.delete(member)
            except Exception as exc:  # noqa: BLE001
                remote_errors.append(f"Mitglied {jf.stem}: {exc}")

    remote_errors.extend(_cleanup_remote_for_record(record, db))
    backup_engine.delete_backup(Path(record.path))
    db.delete(record)
    db.commit()
    if remote_errors:
        return {"ok": True, "warning": "Lokal gelöscht, Probleme bei Speicherzielen: " + "; ".join(remote_errors)}
    return {"ok": True}


class LandscapeBackupPayload(BaseModel):
    label: Optional[str] = None
    project_filter: Optional[str] = None
    storage_target_ids: Optional[list[int]] = None
    stream_volumes_target_id: Optional[int] = None
    stop_containers: bool = False


def _run_landscape_job(job_id: str, label: Optional[str], project_filter: Optional[str],
                        storage_target_ids: Optional[list[int]],
                        stream_volumes_target_id: Optional[int] = None,
                        stop_containers: bool = False):
    db = SessionLocal()
    try:
        landscape_label = label or "Gesamte Landschaft"
        event_log.log_event("backup", f"Landschafts-Backup „{landscape_label}“ gestartet")

        def progress(step, name, total=None):
            job_tracker.update_progress(job_id, step, name, total)

        def on_bytes(n):
            job_tracker.update_bytes(job_id, n)

        def on_upload_bytes(n):
            job_tracker.update_upload_bytes(job_id, n)

        def on_log(msg):
            job_tracker.add_log(job_id, msg)

        stream_target = storage_sync.resolve_stream_target(db, stream_volumes_target_id)
        result = backup_engine.backup_landscape(
            BACKUPS_DIR, project_filter=project_filter, label=label, on_progress=progress,
            stream_target=stream_target, should_cancel=lambda: job_tracker.is_cancel_requested(job_id),
            stop_containers=stop_containers, on_bytes=on_bytes, on_upload_bytes=on_upload_bytes,
            on_log=on_log,
        )
        record = BackupRecord(
            backup_type="landscape",
            name=result.name,
            path=str(result.path),
            status="ok" if result.ok else "failed",
            error=result.error,
            size_bytes=result.size_bytes,
            containers_json=json.dumps(result.containers),
            streamed_target_id=result.streamed_target_id,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        # Each member container backup lives in its own directory with real
        # data (image, volumes) - without its own record it'd be invisible in
        # the UI, never deletable, and never subject to retention, even
        # though it still takes up real disk space.
        for member in result.member_results:
            db.add(BackupRecord(
                backup_type="container", name=member.name, path=str(member.path),
                status="ok" if member.ok else "failed", error=member.error,
                size_bytes=member.size_bytes, containers_json=json.dumps([member.name]),
                streamed_target_id=member.streamed_target_id,
            ))
        db.commit()

        if result.ok:
            def upload_progress(label_, idx, total):
                job_tracker.update_progress(job_id, 1, label_, 1)

            if storage_target_ids is None:
                sync_results = storage_sync.sync_to_all_targets(result.path, on_progress=upload_progress)
            else:
                sync_results = storage_sync.sync_to_selected_targets(result.path, storage_target_ids, on_progress=upload_progress)
            record.synced_target_ids = json.dumps([r["target_id"] for r in sync_results if r["ok"]])
            db.commit()

        if result.cancelled:
            job_tracker.cancel_job(job_id)
            event_log.log_event("backup", f"Landschafts-Backup „{landscape_label}“ abgebrochen", level="error")
        else:
            job_tracker.finish_job(job_id, result.ok, result.error, record.id)
            if result.ok:
                event_log.log_event("backup", f"Landschafts-Backup „{landscape_label}“ erfolgreich abgeschlossen")
            else:
                event_log.log_event("backup", f"Landschafts-Backup „{landscape_label}“ fehlgeschlagen: {result.error}",
                                     level="error")
    except Exception as exc:  # noqa: BLE001
        job_tracker.finish_job(job_id, False, str(exc))
        event_log.log_event("backup", f"Landschafts-Backup „{landscape_label}“ fehlgeschlagen: {exc}", level="error")
    finally:
        db.close()


@router.post("/landscape")
def backup_landscape_now(payload: LandscapeBackupPayload, user: User = Depends(get_current_user)):
    job = job_tracker.create_job("backup", payload.label or "landscape", total_steps=1)
    thread = threading.Thread(
        target=_run_landscape_job,
        args=(job.id, payload.label, payload.project_filter, payload.storage_target_ids,
              payload.stream_volumes_target_id, payload.stop_containers),
        daemon=True,
    )
    thread.start()
    return {"job_id": job.id}


class RestorePayload(BaseModel):
    new_name: Optional[str] = None
    start: bool = True


def _run_restore_job(job_id: str, backup_path: str, new_name: Optional[str], start: bool,
                      stream_target: Optional[tuple]):
    label = new_name or Path(backup_path).parent.name
    event_log.log_event("restore", f"Restore von „{label}“ gestartet")
    try:
        def progress(step, name, total=None):
            job_tracker.update_progress(job_id, step, name, total)

        restore_container(Path(backup_path), new_name=new_name, start=start, on_progress=progress,
                           stream_target=stream_target)
        job_tracker.finish_job(job_id, True)
        event_log.log_event("restore", f"Restore von „{label}“ erfolgreich abgeschlossen")
    except Exception as exc:  # noqa: BLE001
        job_tracker.finish_job(job_id, False, str(exc))
        event_log.log_event("restore", f"Restore von „{label}“ fehlgeschlagen: {exc}", level="error")


@router.post("/{backup_id}/restore")
def restore_backup(backup_id: int, payload: RestorePayload, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    record = db.query(BackupRecord).filter(BackupRecord.id == backup_id).first()
    if not record:
        raise HTTPException(404, "Backup not found")
    if record.backup_type != "container":
        raise HTTPException(400, "Only single-container backups can be restored directly; "
                                  "restore each member container of a landscape backup individually")

    stream_target = storage_sync.resolve_stream_target(db, record.streamed_target_id)
    job = job_tracker.create_job("restore", record.name, total_steps=1)
    thread = threading.Thread(
        target=_run_restore_job,
        args=(job.id, record.path, payload.new_name, payload.start, stream_target),
        daemon=True,
    )
    thread.start()
    return {"job_id": job.id}


@router.get("/{backup_id}/members")
def landscape_members(backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """For a landscape backup, resolve each member container name to the
    BackupRecord from the SAME run (not just the most-recent one).
    The landscape directory contains per-member JSON files with the exact
    backup_path, which we use to look up the matching record."""
    record = db.query(BackupRecord).filter(BackupRecord.id == backup_id).first()
    if not record or record.backup_type != "landscape":
        raise HTTPException(404, "Landscape backup not found")

    landscape_dir = Path(record.path)
    result = []

    if landscape_dir.exists():
        # Use the per-member JSON files written during the backup run.
        # Each file: {"container_name": "...", "backup_path": "..."}
        json_files = sorted(landscape_dir.glob("*.json"))
        for jf in json_files:
            try:
                data = json.loads(jf.read_text())
                container_name = data.get("container_name", jf.stem)
                backup_path = data.get("backup_path", "")
                candidate = (
                    db.query(BackupRecord)
                    .filter(BackupRecord.path == backup_path, BackupRecord.name == container_name)
                    .first()
                )
                result.append({
                    "container_name": container_name,
                    "backup_id": candidate.id if candidate else None,
                    "created_at": candidate.created_at.isoformat() + "Z" if candidate else None,
                    "status": candidate.status if candidate else None,
                })
            except Exception:
                pass
    else:
        # Fallback: containers_json name list, match by latest-in-time
        members = json.loads(record.containers_json) if record.containers_json else []
        for member_name in members:
            candidate = (
                db.query(BackupRecord)
                .filter(BackupRecord.name == member_name, BackupRecord.backup_type == "container")
                .order_by(BackupRecord.created_at.desc())
                .first()
            )
            result.append({
                "container_name": member_name,
                "backup_id": candidate.id if candidate else None,
                "created_at": candidate.created_at.isoformat() + "Z" if candidate else None,
                "status": candidate.status if candidate else None,
            })

    return {"members": result}
