import importlib

import pytest


def test_build_auth_url_requires_public_url(monkeypatch):
    from app import config, oauth_storage

    monkeypatch.delenv("DBM_PUBLIC_URL", raising=False)
    monkeypatch.setenv("DBM_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("DBM_GOOGLE_CLIENT_SECRET", "secret")
    try:
        importlib.reload(config)
        importlib.reload(oauth_storage)
        with pytest.raises(oauth_storage.OAuthNotConfigured):
            oauth_storage.build_auth_url("google")
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(oauth_storage)


def test_build_auth_url_requires_client_credentials(monkeypatch):
    from app import config, oauth_storage

    monkeypatch.setenv("DBM_PUBLIC_URL", "http://192.168.1.10:8420")
    monkeypatch.delenv("DBM_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("DBM_GOOGLE_CLIENT_SECRET", raising=False)
    try:
        importlib.reload(config)
        importlib.reload(oauth_storage)
        with pytest.raises(oauth_storage.OAuthNotConfigured):
            oauth_storage.build_auth_url("google")
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(oauth_storage)


def test_build_auth_url_returns_state_matching_pending_lookup(monkeypatch):
    from app import config, oauth_storage

    monkeypatch.setenv("DBM_PUBLIC_URL", "http://192.168.1.10:8420")
    monkeypatch.setenv("DBM_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("DBM_GOOGLE_CLIENT_SECRET", "secret")
    try:
        importlib.reload(config)
        importlib.reload(oauth_storage)
        url, state = oauth_storage.build_auth_url("google")
        assert "cid" in url
        assert state
        with pytest.raises(ValueError):
            oauth_storage.pop_pending(state)  # nothing exchanged yet - not pending
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(oauth_storage)


def test_pop_pending_unknown_state_raises():
    from app import oauth_storage
    with pytest.raises(ValueError):
        oauth_storage.pop_pending("does-not-exist")


def test_oauth_complete_creates_target_and_redacts_refresh_token():
    from app import oauth_storage
    from app.main import app
    from app.reset_password import reset_password
    from fastapi.testclient import TestClient

    reset_password("oauth-complete-user", "supersecret1")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "oauth-complete-user", "password": "supersecret1"})

        state = "test-state-123"
        oauth_storage._pending[state] = {
            "provider": "google", "refresh_token": "super-secret-token",
            "account": "me@example.com", "created_at": __import__("time").time(),
        }

        resp = client.post("/api/settings/storage-targets/oauth-complete", json={
            "state": state, "name": "My Drive", "folder_path": "/docker-backups/",
        })
        assert resp.status_code == 200
        target_id = resp.json()["id"]

        # The state is single-use - popped once consumed.
        assert state not in oauth_storage._pending

        listed = client.get("/api/settings/storage-targets").json()["targets"]
        target = next(t for t in listed if t["id"] == target_id)
        assert target["type"] == "google_drive"
        assert target["config"]["connected"] is True
        assert "refresh_token" not in target["config"]
        assert target["config"]["folder_path"] == "docker-backups"

        # Reconnecting (target_id passed) updates the same row instead of creating a new one.
        state2 = "test-state-456"
        oauth_storage._pending[state2] = {
            "provider": "google", "refresh_token": "rotated-token",
            "account": "me@example.com", "created_at": __import__("time").time(),
        }
        resp2 = client.post("/api/settings/storage-targets/oauth-complete", json={
            "state": state2, "name": "My Drive", "folder_path": "docker-backups", "target_id": target_id,
        })
        assert resp2.status_code == 200
        assert resp2.json()["id"] == target_id

        listed2 = client.get("/api/settings/storage-targets").json()["targets"]
        assert len([t for t in listed2 if t["type"] == "google_drive"]) == 1


def test_oauth_callback_escapes_attacker_controlled_error(monkeypatch):
    from app.main import app
    from app.reset_password import reset_password
    from fastapi.testclient import TestClient

    reset_password("oauth-test-user", "supersecret1")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "oauth-test-user", "password": "supersecret1"})
        resp = client.get("/api/settings/oauth/google/callback", params={
            "error": "</script><script>alert(1)</script>",
        })
        assert resp.status_code == 200
        assert "<script>alert(1)</script>" not in resp.text
        assert "alert(1)" in resp.text  # message text itself is still shown, just escaped
