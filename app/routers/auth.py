from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import any_user_exists, get_current_user, hash_password, verify_password
from app.database import get_db
from app.models import User
from fastapi import Request

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SetupPayload(BaseModel):
    username: str
    password: str


class LoginPayload(BaseModel):
    username: str
    password: str


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return {"setup_required": not any_user_exists(db)}


@router.post("/setup")
def setup(payload: SetupPayload, request: Request, db: Session = Depends(get_db)):
    if any_user_exists(db):
        raise HTTPException(400, "Setup already completed")
    if len(payload.username) < 3 or len(payload.password) < 8:
        raise HTTPException(400, "Username min 3 chars, password min 8 chars")
    user = User(username=payload.username, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session["user_id"] = user.id
    return {"ok": True}


@router.post("/login")
def login(payload: LoginPayload, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    request.session["user_id"] = user.id
    return {"ok": True}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username}


class PasswordChangePayload(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(payload: PasswordChangePayload, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "Current password incorrect")
    if len(payload.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}
