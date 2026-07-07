import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    failed_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id = Column(Integer, primary_key=True)
    backup_type = Column(String(16), nullable=False)  # "container" | "landscape"
    name = Column(String(255), nullable=False)  # container name or landscape label
    path = Column(String(1024), nullable=False)  # absolute path to this version's folder
    status = Column(String(16), nullable=False, default="ok")  # ok | failed
    error = Column(Text, nullable=True)
    size_bytes = Column(Integer, default=0)
    containers_json = Column(Text, nullable=True)  # for landscape: JSON list of member container names
    source_image = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    synced_target_ids = Column(Text, nullable=True)  # JSON list of StorageTarget ids this version was uploaded to
    streamed_target_id = Column(Integer, nullable=True)  # set if volumes were streamed directly to this StorageTarget instead of being written locally


class StorageTarget(Base):
    __tablename__ = "storage_targets"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    type = Column(String(16), nullable=False)  # "local_path" | "s3" | "rclone"
    config_json = Column(Text, nullable=False, default="{}")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String(16), nullable=True)
    last_sync_error = Column(Text, nullable=True)


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    target_type = Column(String(16), nullable=False)  # "container" | "landscape"
    target_ref = Column(String(255), nullable=True)  # container name, empty for landscape
    project_filter = Column(String(255), nullable=True)  # landscape only: limit to one Compose project (e.g. multi-container apps like Immich/Nextcloud); empty = entire landscape
    name_contains = Column(String(255), nullable=True)  # landscape only: alternative to project_filter for apps without a Compose project label (e.g. Nextcloud AIO) - matches container names by substring
    cron_expression = Column(String(64), nullable=False)  # standard 5-field cron
    retention_count = Column(Integer, default=7)
    retention_days = Column(Integer, default=0)
    storage_target_ids = Column(Text, nullable=False, default="[]")  # JSON list of StorageTarget ids to sync to
    stream_volumes_target_id = Column(Integer, nullable=True)  # if set, volumes are streamed directly here instead of being written locally first (bypasses at-rest encryption for volume data)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String(16), nullable=True)
