from fastapi.testclient import TestClient

from app import event_log


def test_log_event_persists_and_list_entries_returns_newest_first():
    event_log.log_event("backup", "first message")
    event_log.log_event("restore", "second message", level="error")

    entries = event_log.list_entries(limit=10)
    messages = [e.message for e in entries]
    assert "second message" in messages
    assert "first message" in messages
    assert messages.index("second message") < messages.index("first message")

    second = next(e for e in entries if e.message == "second message")
    assert second.level == "error"
    assert second.category == "restore"


def test_log_event_respects_limit():
    for i in range(5):
        event_log.log_event("backup", f"limit-test-{i}")

    entries = event_log.list_entries(limit=2)
    assert len(entries) == 2


def test_logs_api_requires_auth():
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/logs")
        assert resp.status_code == 401


def test_logs_api_returns_entries():
    from app.main import app
    from app.reset_password import reset_password

    reset_password("logs-user", "supersecret1")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "logs-user", "password": "supersecret1"})

        event_log.log_event("schedule", "api-test message")

        resp = client.get("/api/logs?limit=5")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert any(e["message"] == "api-test message" for e in entries)
        assert all({"id", "created_at", "level", "category", "message"} <= e.keys() for e in entries)
