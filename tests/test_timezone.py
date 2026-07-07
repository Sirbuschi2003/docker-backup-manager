import importlib

from fastapi.testclient import TestClient


def test_tz_name_defaults_to_utc(monkeypatch):
    from app import config

    monkeypatch.delenv("DBM_TZ", raising=False)
    try:
        importlib.reload(config)
        assert config.TZ_NAME == "UTC"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_tz_name_respects_dbm_tz_env_var(monkeypatch):
    from app import config

    monkeypatch.setenv("DBM_TZ", "Europe/Berlin")
    try:
        importlib.reload(config)
        assert config.TZ_NAME == "Europe/Berlin"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_invalid_tz_name_falls_back_to_utc_instead_of_crashing(monkeypatch):
    from app import config

    monkeypatch.setenv("DBM_TZ", "Not/A_Real_Zone")
    try:
        importlib.reload(config)
        assert config.TZ_NAME == "UTC"
        assert config.TZ_ERROR is not None
        assert "Not/A_Real_Zone" in config.TZ_ERROR
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_valid_tz_name_has_no_error(monkeypatch):
    from app import config

    monkeypatch.setenv("DBM_TZ", "Europe/Berlin")
    try:
        importlib.reload(config)
        assert config.TZ_ERROR is None
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_add_or_update_job_uses_configured_timezone_not_system_default(monkeypatch):
    from app import config, scheduler

    monkeypatch.setenv("DBM_TZ", "Europe/Berlin")
    try:
        importlib.reload(config)
        importlib.reload(scheduler)

        from app.models import Schedule
        sched = Schedule(
            id=999999, name="tz-test", target_type="landscape",
            cron_expression="0 3 * * *", storage_target_ids="[]",
        )
        scheduler.add_or_update_job(sched)
        try:
            job = scheduler.scheduler.get_job(scheduler._job_id(sched.id))
            assert str(job.trigger.timezone) == "Europe/Berlin"
        finally:
            scheduler.remove_job(sched.id)
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(scheduler)


def test_overview_reports_server_time_and_timezone():
    from app.main import app
    from app.reset_password import reset_password

    reset_password("tz-overview-user", "supersecret1")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tz-overview-user", "password": "supersecret1"})
        resp = client.get("/api/settings/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "server_time" in data
        assert "timezone" in data
        assert data["timezone"]
