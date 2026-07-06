from fastapi.testclient import TestClient


def test_app_boots_and_setup_login_flow_works():
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/healthz")
        assert r.status_code == 200

        r = client.get("/api/auth/status")
        assert r.json()["setup_required"] is True

        r = client.post("/api/auth/setup", json={"username": "admin", "password": "supersecret1"})
        assert r.status_code == 200

        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["username"] == "admin"

        r = client.get("/api/settings/overview")
        assert r.status_code == 200
        assert "backups_dir" in r.json()

        r = client.post("/api/auth/logout")
        assert r.status_code == 200

        r = client.get("/api/auth/me")
        assert r.status_code == 401

        r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401

        r = client.post("/api/auth/login", json={"username": "admin", "password": "supersecret1"})
        assert r.status_code == 200

        r = client.get("/")
        assert r.status_code == 200
        assert "Docker Backup Manager" in r.text
