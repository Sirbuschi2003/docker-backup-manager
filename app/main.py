from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import scheduler
from app.config import SECRET_KEY, SESSION_COOKIE_NAME, SESSION_MAX_AGE
from app.database import init_db
from app.routers import auth, backups, containers, jobs, schedules, settings

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Docker Backup Manager")

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie=SESSION_COOKIE_NAME,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
)

app.include_router(auth.router)
app.include_router(containers.router)
app.include_router(backups.router)
app.include_router(schedules.router)
app.include_router(settings.router)
app.include_router(jobs.router)

app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/{full_path:path}")
def spa(full_path: str):
    return FileResponse(STATIC_DIR / "index.html")


@app.on_event("startup")
def on_startup():
    init_db()
    scheduler.start()


@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown()
