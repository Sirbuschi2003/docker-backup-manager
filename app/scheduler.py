from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import backup_engine, job_tracker, storage_sync
from app.config import BACKUPS_DIR, TZ_NAME
from app.database import SessionLocal
from app.models import BackupRecord, Schedule
from app.retention import VersionInfo, versions_to_prune

logger = logging.getLogger("dbm.scheduler")

scheduler = BackgroundScheduler(timezone=TZ_NAME)


def _job_id(schedule_id: int) -> str:
    return f"schedule-{schedule_id}"


def run_schedule(schedule_id: int):
    db = SessionLocal()
    try:
        sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not sched or not sched.enabled:
            return

        job = job_tracker.create_job("backup", sched.name, total_steps=1)

        def progress(step, name, total=None):
            job_tracker.update_progress(job.id, step, name, total)

        try:
            stream_target = storage_sync.resolve_stream_target(db, sched.stream_volumes_target_id)
            if sched.target_type == "container":
                result = backup_engine.backup_container(sched.target_ref, BACKUPS_DIR, on_progress=progress,
                                                          stream_target=stream_target)
            else:
                result = backup_engine.backup_landscape(BACKUPS_DIR, project_filter=sched.project_filter,
                                                          label=sched.name, on_progress=progress,
                                                          stream_target=stream_target)

            record = BackupRecord(
                backup_type=sched.target_type,
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

            # Each member container backup of a landscape run lives in its own
            # directory with real data (image, volumes) - without its own
            # record it'd be invisible in the UI, never deletable, and never
            # subject to retention, even though it still takes up disk space.
            for member in result.member_results:
                db.add(BackupRecord(
                    backup_type="container", name=member.name, path=str(member.path),
                    status="ok" if member.ok else "failed", error=member.error,
                    size_bytes=member.size_bytes, containers_json=json.dumps([member.name]),
                    streamed_target_id=member.streamed_target_id,
                ))
            db.commit()

            if result.ok:
                def upload_progress(label, idx, total):
                    progress(1, label, 1)

                target_ids = json.loads(sched.storage_target_ids or "[]")
                sync_results = storage_sync.sync_to_selected_targets(result.path, target_ids, on_progress=upload_progress)
                record.synced_target_ids = json.dumps([r["target_id"] for r in sync_results if r["ok"]])
                db.commit()

            job_tracker.finish_job(job.id, result.ok, result.error, record.id)

            _apply_retention(db, sched)

            sched.last_run_at = datetime.datetime.utcnow()
            sched.last_status = "ok" if result.ok else "failed"
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scheduled backup failed")
            job_tracker.finish_job(job.id, False, str(exc))
            sched.last_run_at = datetime.datetime.utcnow()
            sched.last_status = "failed"
            db.commit()
    finally:
        db.close()


def _apply_retention(db, sched: Schedule):
    from app.models import StorageTarget
    from app.storage_sync import _relative_key

    records = db.query(BackupRecord).filter(BackupRecord.name == sched.name).all()
    if sched.target_type == "container" and sched.target_ref:
        records = db.query(BackupRecord).filter(BackupRecord.name == sched.target_ref).all()

    versions = [VersionInfo(id=r.id, created_at=r.created_at) for r in records]
    prune_ids = {v.id for v in versions_to_prune(versions, sched.retention_count, sched.retention_days)}
    if not prune_ids:
        return
    for r in records:
        if r.id not in prune_ids:
            continue
        try:
            target_ids_to_clean = set(json.loads(r.synced_target_ids or "[]"))
            if r.streamed_target_id is not None:
                target_ids_to_clean.add(r.streamed_target_id)
            for target_id in target_ids_to_clean:
                target = db.query(StorageTarget).filter(StorageTarget.id == target_id).first()
                if not target:
                    continue
                try:
                    storage_sync.delete_from_target(target.type, target.config_json, _relative_key(Path(r.path)))
                except Exception:  # noqa: BLE001
                    logger.exception("Retention: failed to remove %s from target %s", r.path, target.name)
            backup_engine.delete_backup(r.path)
            db.delete(r)
            # Commit per record: if a later deletion in this batch fails, already
            # processed ones must not be rolled back or re-attempted next run,
            # since their local files are already gone from disk at this point.
            db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Retention: failed to prune backup %s (id=%s)", r.path, r.id)
            db.rollback()


def add_or_update_job(sched: Schedule):
    scheduler.add_job(
        run_schedule,
        trigger=CronTrigger.from_crontab(sched.cron_expression, timezone=TZ_NAME),
        args=[sched.id],
        id=_job_id(sched.id),
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def remove_job(schedule_id: int):
    try:
        scheduler.remove_job(_job_id(schedule_id))
    except Exception:  # noqa: BLE001
        pass


def load_all_schedules():
    db = SessionLocal()
    try:
        for sched in db.query(Schedule).filter(Schedule.enabled == True).all():  # noqa: E712
            try:
                add_or_update_job(sched)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to schedule job %s", sched.id)
    finally:
        db.close()


def start():
    if not scheduler.running:
        scheduler.start()
    load_all_schedules()


def shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)
