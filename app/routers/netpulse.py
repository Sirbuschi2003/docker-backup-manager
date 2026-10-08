"""Einstellungen der optionalen NetPulse-Kopplung (siehe app/netpulse.py)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import netpulse
from app.auth import get_admin_user
from app.models import User

router = APIRouter(prefix="/api/netpulse", tags=["netpulse"])


class NetPulsePayload(BaseModel):
    enabled: bool = False
    url: str = ""
    # leer = gespeicherten Schlüssel behalten
    token: Optional[str] = None
    verify_tls: bool = True
    pause: bool = True
    report: bool = True
    grace_s: int = 180
    max_minutes: int = 240


def _merged(payload: NetPulsePayload) -> dict:
    cfg = payload.model_dump()
    if not cfg.get("token"):
        cfg["token"] = netpulse.get_config().get("token", "")
    return cfg


@router.get("")
def get_settings(user: User = Depends(get_admin_user)):
    return netpulse.public_config()


@router.put("")
def save_settings(payload: NetPulsePayload, user: User = Depends(get_admin_user)):
    cfg = _merged(payload)
    if cfg["enabled"] and not (cfg["url"] and cfg["token"]):
        raise HTTPException(400, "Zum Einschalten bitte NetPulse-Adresse und Schlüssel eintragen")
    saved = netpulse.save_config(cfg)
    if saved["enabled"]:
        netpulse.push_inventory_async()
    return netpulse.public_config(saved)


@router.post("/test")
def test_connection(payload: NetPulsePayload, user: User = Depends(get_admin_user)):
    try:
        return netpulse.test(_merged(payload))
    except netpulse.NetPulseError as exc:
        raise HTTPException(400, f"NetPulse: {exc}") from exc
