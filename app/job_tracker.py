"""In-memory tracker for long-running backup/restore jobs so the UI can show
a progress bar with step name, percentage and elapsed time. Jobs are transient
by design (a page reload always re-syncs from the persisted BackupRecord once
a job finishes), so no DB persistence is needed here.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

_lock = threading.Lock()
_jobs: dict[str, "Job"] = {}
# (timestamp, cumulative bytes_done) samples per job, newest-trimmed to the
# last ~10s, used to compute a live transfer speed - see update_bytes().
_byte_samples: dict[str, deque] = {}


@dataclass
class Job:
    id: str
    kind: str  # "backup" | "restore"
    label: str
    total_steps: int = 1
    current_step: int = 0
    step_name: str = "starting"
    status: str = "running"  # running | success | failed | cancelling | cancelled
    error: Optional[str] = None
    result_backup_id: Optional[int] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    cancel_requested: bool = False
    bytes_done: int = 0

    def to_dict(self):
        elapsed = (self.finished_at or time.time()) - self.started_at
        pct = int((self.current_step / self.total_steps) * 100) if self.total_steps else 0
        pct = max(0, min(100, pct))
        eta_seconds = None
        if self.status == "running" and self.current_step > 0:
            per_step = elapsed / self.current_step
            remaining_steps = max(self.total_steps - self.current_step, 0)
            eta_seconds = round(per_step * remaining_steps, 1)
        speed_bytes_per_sec = None
        if self.status == "running":
            samples = _byte_samples.get(self.id)
            if samples and len(samples) >= 2:
                t0, b0 = samples[0]
                t1, b1 = samples[-1]
                dt = t1 - t0
                if dt > 0.2:
                    speed_bytes_per_sec = round((b1 - b0) / dt)
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
            "cancellable": self.status == "running" and not self.cancel_requested,
            "bytes_done": self.bytes_done,
            "speed_bytes_per_sec": speed_bytes_per_sec,
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


def update_bytes(job_id: str, delta: int):
    """Adds delta to the job's cumulative bytes transferred and records a
    timestamped sample so to_dict() can derive a live speed (bytes/sec) from
    the last ~10s of samples, rather than an all-time average that would be
    skewed by fast metadata-only steps."""
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.bytes_done += delta
        now = time.time()
        samples = _byte_samples.setdefault(job_id, deque())
        samples.append((now, job.bytes_done))
        cutoff = now - 10
        while len(samples) > 2 and samples[0][0] < cutoff:
            samples.popleft()


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


def cancel_job(job_id: str, message: str = "Vom Nutzer abgebrochen"):
    """Marks a job as cancelled once the running operation has actually
    stopped (called by the job runner after catching a BackupCancelled)."""
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.status = "cancelled"
        job.error = message
        job.finished_at = time.time()


def request_cancel(job_id: str) -> bool:
    """Signals a running job to stop at its next checkpoint (cooperative -
    the job itself has to poll is_cancel_requested()). Returns False if the
    job doesn't exist or isn't running."""
    with _lock:
        job = _jobs.get(job_id)
        if not job or job.status != "running":
            return False
        job.cancel_requested = True
        job.status = "cancelling"
        return True


def is_cancel_requested(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job.cancel_requested)


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
            _byte_samples.pop(j.id, None)
