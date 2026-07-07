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


def test_request_cancel_marks_job_cancelling_and_sets_flag():
    job = job_tracker.create_job("backup", "app")
    assert job_tracker.is_cancel_requested(job.id) is False

    assert job_tracker.request_cancel(job.id) is True
    assert job_tracker.is_cancel_requested(job.id) is True
    assert job_tracker.get_job(job.id).status == "cancelling"
    assert job_tracker.get_job(job.id).to_dict()["cancellable"] is False


def test_request_cancel_fails_for_already_finished_job():
    job = job_tracker.create_job("backup", "app")
    job_tracker.finish_job(job.id, ok=True)
    assert job_tracker.request_cancel(job.id) is False


def test_request_cancel_fails_for_unknown_job():
    assert job_tracker.request_cancel("does-not-exist") is False


def test_cancel_job_sets_cancelled_status():
    job = job_tracker.create_job("backup", "app")
    job_tracker.request_cancel(job.id)
    job_tracker.cancel_job(job.id)
    d = job_tracker.get_job(job.id).to_dict()
    assert d["status"] == "cancelled"
    assert d["error"] == "Vom Nutzer abgebrochen"
    assert d["cancellable"] is False


def test_running_job_is_cancellable_in_to_dict():
    job = job_tracker.create_job("backup", "app")
    assert job.to_dict()["cancellable"] is True
