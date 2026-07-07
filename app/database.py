from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def _add_missing_columns():
    # Lightweight migration: create_all() only creates missing *tables*, so columns added
    # to existing models in later versions need to be added by hand for already-deployed DBs.
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        if "users" in tables:
            existing = {col["name"] for col in inspector.get_columns("users")}
            if "failed_attempts" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0"))
            if "locked_until" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN locked_until DATETIME"))

        if "schedules" in tables:
            existing = {col["name"] for col in inspector.get_columns("schedules")}
            if "storage_target_ids" not in existing:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN storage_target_ids TEXT NOT NULL DEFAULT '[]'"))
            if "project_filter" not in existing:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN project_filter TEXT"))
            if "stream_volumes_target_id" not in existing:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN stream_volumes_target_id INTEGER"))
            if "name_contains" not in existing:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN name_contains TEXT"))
            if "stop_containers" not in existing:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN stop_containers BOOLEAN NOT NULL DEFAULT 0"))

        if "backup_records" in tables:
            existing = {col["name"] for col in inspector.get_columns("backup_records")}
            if "synced_target_ids" not in existing:
                conn.execute(text("ALTER TABLE backup_records ADD COLUMN synced_target_ids TEXT"))
            if "streamed_target_id" not in existing:
                conn.execute(text("ALTER TABLE backup_records ADD COLUMN streamed_target_id INTEGER"))


def init_db():
    from app import models  # noqa: F401  (ensure models are registered)
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
