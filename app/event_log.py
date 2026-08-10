"""Persistent event log for the log overview page - unlike job_tracker (in-memory,
lost on restart), these rows survive app restarts so past activity stays visible."""
import datetime
import logging
import os

from app.database import SessionLocal
from app.models import LogEntry

logger = logging.getLogger("dbm.event_log")

# Default: keep log entries for 90 days; 0 = keep forever
DEFAULT_LOG_RETENTION_DAYS = int(os.environ.get("DBM_LOG_RETENTION_DAYS", "90"))


def log_event(category: str, message: str, level: str = "info") -> None:
    db = SessionLocal()
    try:
        db.add(LogEntry(category=category, message=message, level=level))
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write log entry: [%s] %s", category, message)
    finally:
        db.close()


def purge_old_entries(retention_days: int) -> int:
    """Delete log entries older than retention_days. Returns number of deleted rows."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=retention_days)
    db = SessionLocal()
    try:
        deleted = db.query(LogEntry).filter(LogEntry.created_at < cutoff).delete()
        db.commit()
        return deleted
    except Exception:  # noqa: BLE001
        logger.exception("Failed to purge old log entries")
        return 0
    finally:
        db.close()


def list_entries(limit: int = 200) -> list[LogEntry]:
    db = SessionLocal()
    try:
        return db.query(LogEntry).order_by(LogEntry.created_at.desc()).limit(limit).all()
    finally:
        db.close()


def count_entries() -> int:
    db = SessionLocal()
    try:
        return db.query(LogEntry).count()
    finally:
        db.close()
