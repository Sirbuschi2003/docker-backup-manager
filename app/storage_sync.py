"""
Uploads/replicates a finished backup to configured off-site storage targets.

Supported target types:
  - local_path: any filesystem path already reachable from inside the
    container (e.g. an NFS share, or an SMB share mounted at the host/OS
    level and bind-mounted in). No credentials are entered in the app here -
    authentication happened when the share was mounted outside the app.
  - smb: a *real* in-app SMB/CIFS connection (server, share, username,
    password) using a pure-Python SMB2/3 client (`smbprotocol`) - no host
    mount, no privileged container needed. This is the option to use when you
    want to type a username/password directly into the app. Share names can
    be listed rather than typed via list_smb_shares() (uses the lighter
    `pysmb` library, which - unlike smbprotocol - exposes share enumeration).
  - s3: any S3-compatible object storage (AWS S3, MinIO, Wasabi, Backblaze B2,
    Ceph RGW, ...) via boto3, using access key/secret + endpoint URL.
  - rclone: shells out to the bundled `rclone` binary for every other backend
    rclone supports out of the box - most notably SFTP, WebDAV, B2, etc. The
    admin configures the remote once (rclone.conf, mounted as a secret or
    built via `rclone config`) and just references its name + a destination
    path here.
  - google_drive / onedrive: OAuth-based targets set up entirely from the web
    UI ("Mit Google/Microsoft anmelden") - no rclone.conf, no terminal. See
    app/oauth_storage.py for the actual OAuth + upload implementation; this
    module just dispatches to it.
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


def _smb_remote_root(config: dict, key_root: str) -> str:
    """Pure helper (no network) so the path-building logic is unit-testable."""
    server = config["server"]
    share = config["share"]
    base = config.get("base_path", "").strip("/\\").replace("/", "\\")
    root = f"\\\\{server}\\{share}"
    if base:
        root += "\\" + base
    if key_root and key_root != ".":
        root += "\\" + key_root.replace("/", "\\")
    return root


def _smb_register_session(config: dict):
    import smbclient

    username = config["username"]
    domain = config.get("domain", "").strip()
    if domain:
        username = f"{domain}\\{username}"
    smbclient.register_session(
        config["server"],
        username=username,
        password=config["password"],
        port=int(config.get("port") or 445),
    )


# Share types the SMB protocol itself reports; used to keep the actual data
# shares in the "list available shares" helper below and hide plumbing.
_HIDDEN_SHARE_TYPES = {"IPC", "PRINTER"}


def _filter_browsable_shares(raw_shares: list) -> list[str]:
    """Pure helper (no network) so the filtering logic is unit-testable.
    Takes pysmb SharedDevice-like objects (must have .name and .isSpecial /
    .type) and returns plain share names a user would actually want to pick,
    i.e. not admin/print/IPC shares such as ADMIN$, C$, IPC$, print$."""
    names = []
    for share in raw_shares:
        if getattr(share, "isSpecial", False):
            continue
        if share.name.upper().endswith("$"):
            continue
        names.append(share.name)
    return names


def list_smb_shares(config: dict) -> list[str]:
    """Connects to the given SMB server and returns the names of shares a
    normal user could back up to, so the UI can offer a picker instead of
    requiring the exact share name to be typed in."""
    from smb.SMBConnection import SMBConnection  # imported lazily, only needed for this lookup

    username = config["username"]
    domain = config.get("domain", "").strip()
    server = config["server"]
    port = int(config.get("port") or 445)

    conn = SMBConnection(username, config["password"], "docker-backup-manager", server,
                          domain=domain, use_ntlm_v2=True, is_direct_tcp=True)
    try:
        if not conn.connect(server, port, timeout=15):
            raise RuntimeError("SMB authentication failed")
        return _filter_browsable_shares(conn.listShares())
    finally:
        conn.close()


def sync_smb(backup_path: Path, config: dict) -> None:
    import smbclient  # imported lazily so smbprotocol is only required if SMB targets are used

    _smb_register_session(config)
    key_root = _relative_key(backup_path)
    remote_root = _smb_remote_root(config, key_root)
    smbclient.makedirs(remote_root, exist_ok=True)

    for file_path in Path(backup_path).rglob("*"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(backup_path)
        remote_path = remote_root + "\\" + rel.as_posix().replace("/", "\\")
        remote_dir = remote_path.rsplit("\\", 1)[0]
        smbclient.makedirs(remote_dir, exist_ok=True)
        with open(file_path, "rb") as src, smbclient.open_file(remote_path, mode="wb") as dst:
            shutil.copyfileobj(src, dst)


def sync_rclone(backup_path: Path, config: dict) -> None:
    remote = config["remote"]
    remote_path = config.get("remote_path", "").strip("/")
    dest = f"{remote}:{remote_path}/{_relative_key(backup_path)}".replace("\\", "/")
    cmd = ["rclone", "copy", str(backup_path), dest, "--config", RCLONE_CONFIG_PATH]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"rclone failed: {proc.stderr.strip() or proc.stdout.strip()}")


def sync_google_drive(backup_path: Path, config: dict) -> None:
    from app import oauth_storage
    oauth_storage.sync_google_drive(backup_path, config)


def sync_onedrive(backup_path: Path, config: dict) -> None:
    from app import oauth_storage
    oauth_storage.sync_onedrive(backup_path, config)


SYNC_HANDLERS = {
    "local_path": sync_local_path,
    "smb": sync_smb,
    "s3": sync_s3,
    "rclone": sync_rclone,
    "google_drive": sync_google_drive,
    "onedrive": sync_onedrive,
}


def sync_to_target(backup_path: Path, target_type: str, config_json: str) -> None:
    handler = SYNC_HANDLERS.get(target_type)
    if not handler:
        raise ValueError(f"Unknown storage target type: {target_type}")
    config = json.loads(config_json or "{}")
    handler(Path(backup_path), config)


def _delete_local_path(config: dict, relative_key: str) -> None:
    dest = Path(config["path"]) / relative_key
    if dest.exists():
        shutil.rmtree(dest)


def _delete_s3(config: dict, relative_key: str) -> None:
    import boto3

    session = boto3.session.Session(
        aws_access_key_id=config["access_key"],
        aws_secret_access_key=config["secret_key"],
        region_name=config.get("region") or None,
    )
    s3 = session.client("s3", endpoint_url=config.get("endpoint_url") or None)
    bucket = config["bucket"]
    prefix = "/".join(filter(None, [config.get("prefix", "").strip("/"), relative_key]))
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})


def _delete_smb(config: dict, relative_key: str) -> None:
    import smbclient

    _smb_register_session(config)
    remote_root = _smb_remote_root(config, relative_key)
    if not smbclient.path.exists(remote_root):
        return
    for dirpath, _dirnames, filenames in smbclient.walk(remote_root, topdown=False):
        for filename in filenames:
            smbclient.remove(f"{dirpath}\\{filename}")
        smbclient.rmdir(dirpath)


def _delete_rclone(config: dict, relative_key: str) -> None:
    remote = config["remote"]
    remote_path = config.get("remote_path", "").strip("/")
    dest = f"{remote}:{remote_path}/{relative_key}".replace("\\", "/")
    proc = subprocess.run(
        ["rclone", "purge", dest, "--config", RCLONE_CONFIG_PATH],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0 and "directory not found" not in (proc.stderr or "").lower():
        raise RuntimeError(f"rclone purge failed: {proc.stderr.strip() or proc.stdout.strip()}")


def _delete_google_drive(config: dict, relative_key: str) -> None:
    from app import oauth_storage
    oauth_storage.delete_google_drive(config, relative_key)


def _delete_onedrive(config: dict, relative_key: str) -> None:
    from app import oauth_storage
    oauth_storage.delete_onedrive(config, relative_key)


DELETE_HANDLERS = {
    "local_path": _delete_local_path,
    "smb": _delete_smb,
    "s3": _delete_s3,
    "rclone": _delete_rclone,
    "google_drive": _delete_google_drive,
    "onedrive": _delete_onedrive,
}


def delete_from_target(target_type: str, config_json: str, relative_key: str) -> None:
    """Removes a single backup version's uploaded copy from a storage target.
    Best-effort by convention of the caller - a missing remote copy (already
    deleted, sync never completed, ...) should not be treated as fatal by
    handlers where that's distinguishable, but network/auth errors still raise."""
    handler = DELETE_HANDLERS.get(target_type)
    if not handler:
        raise ValueError(f"Unknown storage target type: {target_type}")
    config = json.loads(config_json or "{}")
    handler(config, relative_key)


def sync_to_all_targets(backup_path: Path, on_progress=None) -> list[dict]:
    """Runs after a successful ad-hoc (manually triggered) backup: syncs to
    every enabled storage target. Scheduled backups instead use
    sync_to_selected_targets() so each schedule can pick specific targets."""
    return _sync_to_targets(backup_path, target_ids=None, on_progress=on_progress)


def sync_to_selected_targets(backup_path: Path, target_ids: list[int], on_progress=None) -> list[dict]:
    """Runs after a successful scheduled backup: syncs only to the explicitly
    selected (and still enabled) storage targets. An empty list means no
    off-site sync for this schedule."""
    return _sync_to_targets(backup_path, target_ids=target_ids, on_progress=on_progress)


def _sync_to_targets(backup_path: Path, target_ids: Optional[list[int]], on_progress=None) -> list[dict]:
    """Never raises - a failed off-site copy must not make the (already
    successful) local backup look failed."""
    from app.database import SessionLocal
    from app.models import StorageTarget
    import datetime

    db = SessionLocal()
    results = []
    try:
        query = db.query(StorageTarget).filter(StorageTarget.enabled == True)  # noqa: E712
        if target_ids is not None:
            if not target_ids:
                return []
            query = query.filter(StorageTarget.id.in_(target_ids))
        targets = query.all()
        for idx, target in enumerate(targets, start=1):
            if on_progress:
                on_progress(f"Uploading to {target.name}", idx, len(targets))
            try:
                sync_to_target(backup_path, target.type, target.config_json)
                target.last_sync_status = "ok"
                target.last_sync_error = None
                results.append({"target": target.name, "target_id": target.id, "ok": True})
            except Exception as exc:  # noqa: BLE001
                logger.exception("Storage sync failed for target %s", target.name)
                target.last_sync_status = "failed"
                target.last_sync_error = str(exc)
                results.append({"target": target.name, "target_id": target.id, "ok": False, "error": str(exc)})
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
    elif target_type == "smb":
        import smbclient
        _smb_register_session(config)
        root = _smb_remote_root(config, "")
        smbclient.makedirs(root, exist_ok=True)
        smbclient.listdir(root)
    elif target_type == "rclone":
        remote = config["remote"]
        proc = subprocess.run(
            ["rclone", "lsd", f"{remote}:", "--config", RCLONE_CONFIG_PATH],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    elif target_type == "google_drive":
        from app import oauth_storage
        oauth_storage.check_google_drive_connection(config)
    elif target_type == "onedrive":
        from app import oauth_storage
        oauth_storage.check_onedrive_connection(config)
    else:
        raise ValueError(f"Unknown storage target type: {target_type}")
