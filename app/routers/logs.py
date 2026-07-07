from fastapi import APIRouter, Depends

from app import event_log
from app.auth import get_current_user
from app.models import User

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def list_logs(limit: int = 200, user: User = Depends(get_current_user)):
    entries = event_log.list_entries(limit=min(limit, 1000))
    return {"entries": [
        {
            "id": e.id,
            "created_at": e.created_at.isoformat() + "Z",
            "level": e.level,
            "category": e.category,
            "message": e.message,
        } for e in entries
    ]}
