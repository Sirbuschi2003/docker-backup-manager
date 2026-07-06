import os
from pathlib import Path

BASE_DIR = Path(os.environ.get("DBM_BASE_DIR", "/data")).resolve()
BACKUPS_DIR = Path(os.environ.get("DBM_BACKUPS_DIR", str(BASE_DIR / "backups"))).resolve()
DB_PATH = Path(os.environ.get("DBM_DB_PATH", str(BASE_DIR / "dbm.sqlite3"))).resolve()

SECRET_KEY = os.environ.get("DBM_SECRET_KEY", "change-me-in-production-please")
SESSION_COOKIE_NAME = "dbm_session"
SESSION_MAX_AGE = int(os.environ.get("DBM_SESSION_MAX_AGE", str(60 * 60 * 24 * 7)))

DOCKER_HELPER_IMAGE = os.environ.get("DBM_HELPER_IMAGE", "alpine:3.20")

DEFAULT_RETENTION_COUNT = int(os.environ.get("DBM_DEFAULT_RETENTION_COUNT", "7"))
DEFAULT_RETENTION_DAYS = int(os.environ.get("DBM_DEFAULT_RETENTION_DAYS", "0"))  # 0 = disabled

BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
