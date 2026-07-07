from fastapi import APIRouter, Depends, HTTPException

from app import job_tracker
from app.auth import get_current_user
from app.models import User

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(active_only: bool = False, user: User = Depends(get_current_user)):
    return {"jobs": [j.to_dict() for j in job_tracker.list_jobs(active_only=active_only)]}


@router.get("/{job_id}")
def get_job(job_id: str, user: User = Depends(get_current_user)):
    job = job_tracker.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, user: User = Depends(get_current_user)):
    job = job_tracker.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job_tracker.request_cancel(job_id):
        raise HTTPException(400, "Job is not running")
    return {"ok": True}
