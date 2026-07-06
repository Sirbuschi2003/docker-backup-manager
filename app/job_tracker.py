"""In-memory tracker for long-running backup/restore jobs so the UI can show
a progress bar with step name, percentage and elapsed time. Jobs are transient
by design (a page reload always re-syncs from the persisted BackupRecord once
a job finishes), so no DB persistence is needed here.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

_lock = threading.Lock()
_jobs: dict[str, "Job"] = {}


@dataclass
class Job:
    id: str
    kind: str  # "backup" | "restore"
    label: str
    total_steps: int = 1
    current_step: int = 0
    step_name: str = "starting"
    status: str = "running"  # running | success | failed
    error: Optional[str] = None
    result_backup_id: Optional[int] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self):
        elapsed = (self.finished_at or time.time()) - self.started_at
        pct = int((self.current_step / self.total_steps) * 100) if self.total_steps else 0
        pct = max(0, min(100, pct))
        eta_seconds = None
        if self.status == "running" and self.current_step > 0:
            per_step = elapsed / self.current_step
            remaining_steps = max(self.total_steps - self.current_step, 0)
            eta_seconds = round(per_step * remaining_steps, 1)
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "step_name": self.step_name,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "percent": pct,
            "elapsed_seconds": round(elapsed, 1),
            "eta_seconds": eta_seconds,
            "error": self.error,
            "result_backup_id": self.result_backup_id,
        }


def create_job(kind: str, label: str, total_steps: int = 1) -> Job:
    job = Job(id=str(uuid.uuid4()), kind=kind, label=label, total_steps=max(total_steps, 1))
    with _lock:
        _jobs[job.id] = job
    return job


def update_progress(job_id: str, current_step: int, step_name: str, total_steps: Optional[int] = None):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.current_step = current_step
        job.step_name = step_name
        if total_steps is not None:
            job.total_steps = max(total_steps, 1)


def finish_job(job_id: str, ok: bool, error: Optional[str] = None, result_backup_id: Optional[int] = None):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.status = "success" if ok else "failed"
        job.error = error
        job.result_backup_id = result_backup_id
        job.finished_at = time.time()
        job.current_step = job.total_steps if ok else job.current_step


def get_job(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)


def list_jobs(active_only: bool = False) -> list[Job]:
    with _lock:
        jobs = list(_jobs.values())
    if active_only:
        jobs = [j for j in jobs if j.status == "running"]
    jobs.sort(key=lambda j: j.started_at, reverse=True)
    return jobs


def prune_old_jobs(max_finished: int = 50):
    with _lock:
        finished = sorted(
            (j for j in _jobs.values() if j.status != "running"),
            key=lambda j: j.finished_at or 0, reverse=True,
        )
        for j in finished[max_finished:]:
            _jobs.pop(j.id, None)
