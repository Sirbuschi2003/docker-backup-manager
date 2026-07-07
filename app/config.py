import os
import secrets
from pathlib import Path

BASE_DIR = Path(os.environ.get("DBM_BASE_DIR", "/data")).resolve()
BACKUPS_DIR = Path(os.environ.get("DBM_BACKUPS_DIR", str(BASE_DIR / "backups"))).resolve()
DB_PATH = Path(os.environ.get("DBM_DB_PATH", str(BASE_DIR / "dbm.sqlite3"))).resolve()

BASE_DIR.mkdir(parents=True, exist_ok=True)


def _load_or_create_secret_key() -> str:
    env_key = os.environ.get("DBM_SECRET_KEY")
    if env_key:
        return env_key
    # No key configured (common for a first-time/non-technical install): generate one
    # and persist it next to the database so sessions survive restarts, instead of
    # falling back to a hardcoded value that would be identical across every install.
    key_path = BASE_DIR / ".secret_key"
    if key_path.exists():
        return key_path.read_text().strip()
    key = secrets.token_hex(32)
    key_path.write_text(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


SECRET_KEY = _load_or_create_secret_key()
SESSION_COOKIE_NAME = "dbm_session"
SESSION_MAX_AGE = int(os.environ.get("DBM_SESSION_MAX_AGE", str(60 * 60 * 24 * 7)))
SESSION_HTTPS_ONLY = os.environ.get("DBM_SESSION_HTTPS_ONLY", "false").lower() in ("1", "true", "yes")

LOGIN_MAX_ATTEMPTS = int(os.environ.get("DBM_LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("DBM_LOGIN_LOCKOUT_SECONDS", str(5 * 60)))

DOCKER_HELPER_IMAGE = os.environ.get("DBM_HELPER_IMAGE", "alpine:3.20")

# OAuth-based storage targets (Google Drive / OneDrive). PUBLIC_URL is the
# address the browser uses to reach this app (e.g. "http://192.168.1.10:8420")
# - needed to build the OAuth redirect URI, since the container can't know
# its own externally-reachable address on its own.
PUBLIC_URL = os.environ.get("DBM_PUBLIC_URL", "").rstrip("/")
GOOGLE_CLIENT_ID = os.environ.get("DBM_GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("DBM_GOOGLE_CLIENT_SECRET", "")
MS_CLIENT_ID = os.environ.get("DBM_MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.environ.get("DBM_MS_CLIENT_SECRET", "")
MS_TENANT = os.environ.get("DBM_MS_TENANT", "common")

DEFAULT_RETENTION_COUNT = int(os.environ.get("DBM_DEFAULT_RETENTION_COUNT", "7"))
DEFAULT_RETENTION_DAYS = int(os.environ.get("DBM_DEFAULT_RETENTION_DAYS", "0"))  # 0 = disabled

BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
