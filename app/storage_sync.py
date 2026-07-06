"""
Uploads/replicates a finished backup to configured off-site storage targets.

Supported target types:
  - local_path: any filesystem path reachable from inside the container. This
    is how SMB and NFS shares are supported: the host mounts the share
    (Synology/QNAP/UGREEN volume config, or /etc/fstab + docker bind mount on
    Ubuntu) at some path, that path is bind-mounted into this container, and
    is then selected here. We just recursively copy into it.
  - s3: any S3-compatible object storage (AWS S3, MinIO, Wasabi, Backblaze B2,
    Ceph RGW, ...) via boto3, using access key/secret + endpoint URL.
  - rclone: shells out to the bundled `rclone` binary for every other backend
    rclone supports out of the box - most notably Google Drive and OneDrive,
    but also SFTP, WebDAV, B2, etc. The admin configures the remote once
    (rclone.conf, mounted as a secret or built via `rclone config`) and just
    references its name + a destination path here.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from app.config import BACKUPS_DIR

logger = logging.getLogger("dbm.storage_sync")

RCLONE_CONFIG_PATH = "/data/rclone.conf"


def _relative_key(backup_path: Path) -> str:
    try:
        return str(Path(backup_path).resolve().relative_to(BACKUPS_DIR.resolve()))
    except ValueError:
        return Path(backup_path).name


def sync_local_path(backup_path: Path, config: dict) -> None:
    dest_root = Path(config["path"])
    dest = dest_root / _relative_key(backup_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(backup_path, dest)


def sync_s3(backup_path: Path, config: dict) -> None:
    import boto3  # imported lazily so boto3 is only required if S3 targets are used

    session = boto3.session.Session(
        aws_access_key_id=config["access_key"],
        aws_secret_access_key=config["secret_key"],
        region_name=config.get("region") or None,
    )
    s3 = session.client("s3", endpoint_url=config.get("endpoint_url") or None)
    bucket = config["bucket"]
    prefix = config.get("prefix", "").strip("/")
    key_root = _relative_key(backup_path)

    for file_path in Path(backup_path).rglob("*"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(backup_path)
        key = "/".join(filter(None, [prefix, key_root, str(rel).replace("\\", "/")]))
        s3.upload_file(str(file_path), bucket, key)


def sync_rclone(backup_path: Path, config: dict) -> None:
    remote = config["remote"]
    remote_path = config.get("remote_path", "").strip("/")
    dest = f"{remote}:{remote_path}/{_relative_key(backup_path)}".replace("\\", "/")
    cmd = ["rclone", "copy", str(backup_path), dest, "--config", RCLONE_CONFIG_PATH]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"rclone failed: {proc.stderr.strip() or proc.stdout.strip()}")


SYNC_HANDLERS = {
    "local_path": sync_local_path,
    "s3": sync_s3,
    "rclone": sync_rclone,
}


def sync_to_target(backup_path: Path, target_type: str, config_json: str) -> None:
    handler = SYNC_HANDLERS.get(target_type)
    if not handler:
        raise ValueError(f"Unknown storage target type: {target_type}")
    config = json.loads(config_json or "{}")
    handler(Path(backup_path), config)


def sync_to_all_targets(backup_path: Path, on_progress=None) -> list[dict]:
    """Runs after a successful backup. Returns per-target results; never raises -
    a failed off-site copy must not make the (already successful) local backup
    look failed."""
    from app.database import SessionLocal
    from app.models import StorageTarget
    import datetime

    db = SessionLocal()
    results = []
    try:
        targets = db.query(StorageTarget).filter(StorageTarget.enabled == True).all()  # noqa: E712
        for idx, target in enumerate(targets, start=1):
            if on_progress:
                on_progress(f"Uploading to {target.name}", idx, len(targets))
            try:
                sync_to_target(backup_path, target.type, target.config_json)
                target.last_sync_status = "ok"
                target.last_sync_error = None
                results.append({"target": target.name, "ok": True})
            except Exception as exc:  # noqa: BLE001
                logger.exception("Storage sync failed for target %s", target.name)
                target.last_sync_status = "failed"
                target.last_sync_error = str(exc)
                results.append({"target": target.name, "ok": False, "error": str(exc)})
            target.last_sync_at = datetime.datetime.utcnow()
            db.commit()
    finally:
        db.close()
    return results


def check_target_connection(target_type: str, config_json: str) -> None:
    """Best-effort connectivity check, raises on failure."""
    config = json.loads(config_json or "{}")
    if target_type == "local_path":
        p = Path(config["path"])
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".dbm_write_test"
        probe.write_text("ok")
        probe.unlink()
    elif target_type == "s3":
        import boto3
        session = boto3.session.Session(
            aws_access_key_id=config["access_key"],
            aws_secret_access_key=config["secret_key"],
            region_name=config.get("region") or None,
        )
        s3 = session.client("s3", endpoint_url=config.get("endpoint_url") or None)
        s3.head_bucket(Bucket=config["bucket"])
    elif target_type == "rclone":
        remote = config["remote"]
        proc = subprocess.run(
            ["rclone", "lsd", f"{remote}:", "--config", RCLONE_CONFIG_PATH],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    else:
        raise ValueError(f"Unknown storage target type: {target_type}")
