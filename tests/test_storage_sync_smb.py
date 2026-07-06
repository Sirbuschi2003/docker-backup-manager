from app.storage_sync import _smb_remote_root


def test_smb_remote_root_basic():
    config = {"server": "192.168.1.50", "share": "backups"}
    assert _smb_remote_root(config, "app1/20240101T000000Z") == \
        "\\\\192.168.1.50\\backups\\app1\\20240101T000000Z"


def test_smb_remote_root_with_base_path():
    config = {"server": "nas", "share": "backups", "base_path": "docker/dbm"}
    assert _smb_remote_root(config, "app1/v1") == "\\\\nas\\backups\\docker\\dbm\\app1\\v1"


def test_smb_remote_root_strips_slashes_from_base_path():
    config = {"server": "nas", "share": "backups", "base_path": "/docker/dbm/"}
    assert _smb_remote_root(config, "") == "\\\\nas\\backups\\docker\\dbm"


def test_smb_remote_root_no_key_root():
    config = {"server": "nas", "share": "backups"}
    assert _smb_remote_root(config, "") == "\\\\nas\\backups"
