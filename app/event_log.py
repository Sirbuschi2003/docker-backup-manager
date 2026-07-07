"""Persistent event log for the log overview page - unlike job_tracker (in-memory,
lost on restart), these rows survive app restarts so past activity stays visible."""
import logging

from app.database import SessionLocal
from app.models import LogEntry

logger = logging.getLogger("dbm.event_log")


def log_event(category: str, message: str, level: str = "info") -> None:
    db = SessionLocal()
    try:
        db.add(LogEntry(category=category, message=message, level=level))
        db.commit()
    except Exception:  # noqa: BLE001
        # A logging failure must never break the backup/restore it's describing.
        logger.exception("Failed to write log entry: [%s] %s", category, message)
    finally:
        db.close()


def list_entries(limit: int = 200) -> list[LogEntry]:
    db = SessionLocal()
    try:
        return db.query(LogEntry).order_by(LogEntry.created_at.desc()).limit(limit).all()
    finally:
        db.close()
