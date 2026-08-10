from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import event_log
from app.auth import get_current_user
from app.models import User

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def list_logs(limit: int = 200, user: User = Depends(get_current_user)):
    entries = event_log.list_entries(limit=min(limit, 2000))
    return {
        "entries": [
            {
                "id": e.id,
                "created_at": e.created_at.isoformat() + "Z",
                "level": e.level,
                "category": e.category,
                "message": e.message,
            } for e in entries
        ],
        "total": event_log.count_entries(),
    }


@router.get("/settings")
def get_log_settings(user: User = Depends(get_current_user)):
    return {"retention_days": event_log.DEFAULT_LOG_RETENTION_DAYS}


class LogSettingsPayload(BaseModel):
    retention_days: int


@router.put("/settings")
def update_log_settings(payload: LogSettingsPayload, user: User = Depends(get_current_user)):
    if payload.retention_days < 0:
        raise HTTPException(400, "retention_days muss 0 (unbegrenzt) oder größer sein")
    event_log.DEFAULT_LOG_RETENTION_DAYS = payload.retention_days
    return {"ok": True, "retention_days": payload.retention_days}


@router.post("/purge")
def purge_logs(user: User = Depends(get_current_user)):
    days = event_log.DEFAULT_LOG_RETENTION_DAYS
    if days <= 0:
        raise HTTPException(400, "Log-Aufbewahrung ist auf unbegrenzt gesetzt — nichts zu löschen.")
    deleted = event_log.purge_old_entries(days)
    return {"deleted": deleted, "retention_days": days}
