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


def test_storage_target_test_endpoint_works_before_saving(tmp_path):
    from app.main import app
    from app.reset_password import reset_password

    reset_password("storage-test-user", "supersecret1")

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "storage-test-user", "password": "supersecret1"})

        ok = client.post("/api/settings/storage-targets/test", json={
            "type": "local_path", "config": {"path": str(tmp_path / "somewhere")},
        })
        assert ok.status_code == 200

        bad = client.post("/api/settings/storage-targets/test", json={
            "type": "smb", "config": {"server": "127.0.0.1", "share": "nope", "username": "x", "password": "x"},
        })
        assert bad.status_code == 400


def test_init_db_migrates_pre_existing_schedules_table_missing_storage_target_ids(tmp_path, monkeypatch):
    import importlib
    import sqlite3

    from app import config, database

    # Simulate a database created before the storage_target_ids column existed
    # (i.e. anything deployed before commit 6fd3365) to reproduce the exact
    # startup crash: "sqlite3.OperationalError: no such column: schedules.storage_target_ids".
    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE schedules (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            target_type VARCHAR(16) NOT NULL,
            target_ref VARCHAR(255),
            cron_expression VARCHAR(64) NOT NULL,
            retention_count INTEGER,
            retention_days INTEGER,
            enabled BOOLEAN,
            created_at DATETIME,
            last_run_at DATETIME,
            last_status VARCHAR(16)
        )
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DBM_DB_PATH", str(db_path))
    try:
        importlib.reload(config)
        importlib.reload(database)

        database.init_db()

        inspector_columns = {col["name"] for col in database.inspect(database.engine).get_columns("schedules")}
        assert "storage_target_ids" in inspector_columns

        from app.models import Schedule
        db = database.SessionLocal()
        try:
            assert db.query(Schedule).all() == []
        finally:
            db.close()
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(database)


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
