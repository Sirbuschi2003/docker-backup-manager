import os
import tempfile

# Must run before any `app.*` module is imported (app.config creates directories
# at import time), so point everything at a throwaway temp dir for the test run.
_tmp_base = tempfile.mkdtemp(prefix="dbm-test-")
os.environ.setdefault("DBM_BASE_DIR", _tmp_base)
os.environ.setdefault("DBM_BACKUPS_DIR", os.path.join(_tmp_base, "backups"))
os.environ.setdefault("DBM_DB_PATH", os.path.join(_tmp_base, "dbm.sqlite3"))
