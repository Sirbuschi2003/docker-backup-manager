from fastapi.testclient import TestClient

from app import job_tracker


def test_cancel_running_job_via_api():
    from app.main import app
    from app.reset_password import reset_password

    reset_password("jobs-cancel-user", "supersecret1")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "jobs-cancel-user", "password": "supersecret1"})

        job = job_tracker.create_job("backup", "app")

        resp = client.post(f"/api/jobs/{job.id}/cancel")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        d = job_tracker.get_job(job.id).to_dict()
        assert d["status"] == "cancelling"
        assert d["cancellable"] is False


def test_cancel_finished_job_returns_400():
    from app.main import app
    from app.reset_password import reset_password

    reset_password("jobs-cancel-user2", "supersecret1")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "jobs-cancel-user2", "password": "supersecret1"})

        job = job_tracker.create_job("backup", "app")
        job_tracker.finish_job(job.id, ok=True)

        resp = client.post(f"/api/jobs/{job.id}/cancel")
        assert resp.status_code == 400


def test_cancel_unknown_job_returns_404():
    from app.main import app
    from app.reset_password import reset_password

    reset_password("jobs-cancel-user3", "supersecret1")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "jobs-cancel-user3", "password": "supersecret1"})

        resp = client.post("/api/jobs/does-not-exist/cancel")
        assert resp.status_code == 404
