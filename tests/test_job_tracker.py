from app import job_tracker


def test_job_lifecycle_progress_and_percent():
    job = job_tracker.create_job("backup", "my-app", total_steps=4)
    assert job.status == "running"

    job_tracker.update_progress(job.id, 2, "Saving image", total_steps=4)
    fetched = job_tracker.get_job(job.id)
    d = fetched.to_dict()
    assert d["percent"] == 50
    assert d["step_name"] == "Saving image"
    assert d["status"] == "running"

    job_tracker.finish_job(job.id, ok=True, result_backup_id=42)
    d = job_tracker.get_job(job.id).to_dict()
    assert d["status"] == "success"
    assert d["result_backup_id"] == 42
    assert d["percent"] == 100


def test_job_failure_keeps_error():
    job = job_tracker.create_job("restore", "other-app", total_steps=1)
    job_tracker.finish_job(job.id, ok=False, error="boom")
    d = job_tracker.get_job(job.id).to_dict()
    assert d["status"] == "failed"
    assert d["error"] == "boom"


def test_list_jobs_active_only_filters_running():
    j1 = job_tracker.create_job("backup", "a")
    j2 = job_tracker.create_job("backup", "b")
    job_tracker.finish_job(j2.id, ok=True)

    active_ids = {j.id for j in job_tracker.list_jobs(active_only=True)}
    assert j1.id in active_ids
    assert j2.id not in active_ids
