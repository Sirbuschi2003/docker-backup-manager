import datetime
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import encryption, oauth_storage, storage_sync
from app.auth import get_current_user
from app.config import BACKUPS_DIR, DEFAULT_RETENTION_COUNT, DEFAULT_RETENTION_DAYS, TZ_ERROR, TZ_NAME
from app.database import get_db
from app.docker_client import is_available
from app.models import StorageTarget, User

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/overview")
def overview(user: User = Depends(get_current_user)):
    import pytz

    docker_ok, docker_error = is_available()
    total_size = 0
    if BACKUPS_DIR.exists():
        total_size = sum(f.stat().st_size for f in BACKUPS_DIR.rglob("*") if f.is_file())
    server_now = datetime.datetime.now(pytz.timezone(TZ_NAME))
    return {
        "backups_dir": str(BACKUPS_DIR),
        "backups_total_bytes": total_size,
        "docker_available": docker_ok,
        "docker_error": docker_error,
        "default_retention_count": DEFAULT_RETENTION_COUNT,
        "default_retention_days": DEFAULT_RETENTION_DAYS,
        "encryption_enabled": encryption.is_enabled(),
        "encryption_error": encryption.config_error(),
        "server_time": server_now.isoformat(),
        "timezone": TZ_NAME,
        "timezone_error": TZ_ERROR,
    }


class StorageTargetPayload(BaseModel):
    name: str
    type: str  # "local_path" | "s3" | "rclone"
    config: dict
    enabled: bool = True


def _target_config_for_response(target_type: str, config: dict) -> dict:
    """The refresh_token grants ongoing account access (far more powerful than
    a single backup share password) and is never needed by the frontend -
    editing an OAuth target only touches name/folder_path, and reconnecting
    fetches a fresh token via the OAuth flow rather than reusing the old one."""
    if target_type in ("google_drive", "onedrive"):
        config = {**config, "connected": bool(config.get("refresh_token"))}
        config.pop("refresh_token", None)
    return config


@router.get("/storage-targets")
def list_targets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(StorageTarget).order_by(StorageTarget.created_at.desc()).all()
    return {"targets": [
        {
            "id": t.id, "name": t.name, "type": t.type,
            "config": _target_config_for_response(t.type, json.loads(t.config_json)),
            "enabled": t.enabled, "last_sync_at": t.last_sync_at.isoformat() + "Z" if t.last_sync_at else None,
            "last_sync_status": t.last_sync_status, "last_sync_error": t.last_sync_error,
        } for t in rows
    ]}


@router.post("/storage-targets")
def create_target(payload: StorageTargetPayload, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    if payload.type not in ("local_path", "smb", "s3", "rclone", "google_drive", "onedrive"):
        raise HTTPException(400, "Invalid target type")
    target = StorageTarget(
        name=payload.name, type=payload.type, config_json=json.dumps(payload.config), enabled=payload.enabled,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return {"id": target.id}


@router.put("/storage-targets/{target_id}")
def update_target(target_id: int, payload: StorageTargetPayload, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    target = db.query(StorageTarget).filter(StorageTarget.id == target_id).first()
    if not target:
        raise HTTPException(404, "Storage target not found")
    config = payload.config
    if target.type in ("google_drive", "onedrive") and target.type == payload.type:
        # The frontend never receives the refresh_token back (see
        # _target_config_for_response), so a naive overwrite here would erase
        # it. Only folder_path (and similar non-secret fields) come from the
        # client for these types - keep the stored refresh_token/account.
        stored = json.loads(target.config_json)
        config = {**stored, **{k: v for k, v in payload.config.items() if k != "refresh_token"}}
    target.name = payload.name
    target.type = payload.type
    target.config_json = json.dumps(config)
    target.enabled = payload.enabled
    db.commit()
    return {"ok": True}


@router.delete("/storage-targets/{target_id}")
def delete_target(target_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target = db.query(StorageTarget).filter(StorageTarget.id == target_id).first()
    if not target:
        raise HTTPException(404, "Storage target not found")
    db.delete(target)
    db.commit()
    return {"ok": True}


class StorageTargetTestPayload(BaseModel):
    type: str
    config: dict


@router.post("/storage-targets/test")
def test_storage_target_config(payload: StorageTargetTestPayload, user: User = Depends(get_current_user)):
    """Tests connection settings before a target has been saved, so mistakes
    (wrong share name, bad credentials, ...) surface immediately in the dialog."""
    try:
        storage_sync.check_target_connection(payload.type, json.dumps(payload.config))
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Connection test failed: {exc}")


class SmbSharesPayload(BaseModel):
    server: str
    username: str
    password: str
    domain: str = ""
    port: str = "445"


@router.post("/smb/shares")
def list_smb_shares(payload: SmbSharesPayload, user: User = Depends(get_current_user)):
    try:
        shares = storage_sync.list_smb_shares(payload.model_dump())
        return {"shares": shares}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Freigaben konnten nicht abgerufen werden: {exc}")


@router.post("/storage-targets/{target_id}/test")
def test_storage_target(target_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target = db.query(StorageTarget).filter(StorageTarget.id == target_id).first()
    if not target:
        raise HTTPException(404, "Storage target not found")
    try:
        storage_sync.check_target_connection(target.type, target.config_json)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Connection test failed: {exc}")


# ---------- Google Drive / OneDrive OAuth ----------

@router.get("/oauth/{provider}/start")
def oauth_start(provider: str, user: User = Depends(get_current_user)):
    if provider not in ("google", "onedrive"):
        raise HTTPException(404, "Unknown provider")
    try:
        url, state = oauth_storage.build_auth_url(provider)
    except oauth_storage.OAuthNotConfigured as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse(url)


_CALLBACK_PAGE = """<!doctype html><html><body>
<p>{message}</p>
<script>
  if (window.opener) {{
    window.opener.postMessage({payload}, window.location.origin);
  }}
  window.close();
</script>
</body></html>"""


def _callback_html(message: str, ok: bool, state: str = "", error: str = "") -> str:
    import html
    # error/state can contain attacker-controlled text (query params on this public
    # redirect endpoint) - json.dumps escapes quotes, but "</script>" inside a string
    # would still close the tag early, so also escape "</" before embedding.
    payload = json.dumps({"dbmOAuth": True, "ok": ok, "state": state, "error": error}).replace("</", "<\\/")
    return _CALLBACK_PAGE.format(message=html.escape(message), payload=payload)


@router.get("/oauth/{provider}/callback", response_class=HTMLResponse)
def oauth_callback(provider: str, code: str = "", state: str = "", error: str = "",
                    user: User = Depends(get_current_user)):
    if provider not in ("google", "onedrive"):
        raise HTTPException(404, "Unknown provider")
    if error:
        return _callback_html(f"Anmeldung fehlgeschlagen: {error}", ok=False, error=error)
    try:
        oauth_storage.handle_callback(provider, code, state)
    except Exception as exc:  # noqa: BLE001
        return _callback_html(f"Anmeldung fehlgeschlagen: {exc}", ok=False, error=str(exc))
    return _callback_html("Erfolgreich verbunden - dieses Fenster kann geschlossen werden.", ok=True, state=state)


class OAuthCompletePayload(BaseModel):
    state: str
    name: str
    folder_path: str = ""
    target_id: Optional[int] = None  # set when reconnecting an existing target, not creating a new one


@router.post("/storage-targets/oauth-complete")
def oauth_complete(payload: OAuthCompletePayload, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    try:
        pending = oauth_storage.pop_pending(payload.state)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    target_type = "google_drive" if pending["provider"] == "google" else "onedrive"
    config = {
        "refresh_token": pending["refresh_token"],
        "account": pending["account"],
        "folder_path": payload.folder_path.strip("/"),
    }

    if payload.target_id is not None:
        target = db.query(StorageTarget).filter(StorageTarget.id == payload.target_id).first()
        if not target:
            raise HTTPException(404, "Storage target not found")
        target.name = payload.name or target.name
        target.type = target_type
        target.config_json = json.dumps(config)
    else:
        target = StorageTarget(
            name=payload.name or pending["account"] or target_type, type=target_type,
            config_json=json.dumps(config), enabled=True,
        )
        db.add(target)
    db.commit()
    db.refresh(target)
    return {"id": target.id, "account": pending["account"]}
