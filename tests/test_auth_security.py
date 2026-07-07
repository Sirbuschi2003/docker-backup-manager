from fastapi.testclient import TestClient


def test_login_locks_out_after_too_many_failed_attempts():
    from app.config import LOGIN_MAX_ATTEMPTS
    from app.main import app
    from app.reset_password import reset_password

    # Create the user directly (rather than via /api/auth/setup, which only works
    # once per install) so this test is independent of whether setup already ran.
    reset_password("lockout-user", "supersecret1")

    with TestClient(app) as client:
        for _ in range(LOGIN_MAX_ATTEMPTS):
            r = client.post("/api/auth/login", json={"username": "lockout-user", "password": "wrong"})
            assert r.status_code == 401

        # Account is now locked, even with the correct password.
        r = client.post("/api/auth/login", json={"username": "lockout-user", "password": "supersecret1"})
        assert r.status_code == 429


def test_reset_password_script_creates_and_resets_user():
    from app.database import SessionLocal, init_db
    from app.models import User
    from app.reset_password import reset_password

    init_db()
    reset_password("recovered-admin", "brandnewpass1")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "recovered-admin").first()
        assert user is not None

        reset_password("recovered-admin", "anothernewpass2")
        db.refresh(user)
        from app.auth import verify_password
        assert verify_password("anothernewpass2", user.password_hash)
        assert user.failed_attempts == 0
        assert user.locked_until is None
    finally:
        db.close()


def test_secret_key_is_generated_and_persisted(tmp_path, monkeypatch):
    import importlib
    from app import config

    monkeypatch.delenv("DBM_SECRET_KEY", raising=False)
    monkeypatch.setenv("DBM_BASE_DIR", str(tmp_path))
    try:
        importlib.reload(config)

        key_file = tmp_path / ".secret_key"
        assert key_file.exists()
        first_key = config.SECRET_KEY
        assert len(first_key) >= 32

        importlib.reload(config)
        assert config.SECRET_KEY == first_key
    finally:
        # Restore the module to its original (test-session) state so later tests
        # that import app.config still see the shared temp dir from conftest.py.
        monkeypatch.undo()
        importlib.reload(config)
