from app.storage_sync import _filter_browsable_shares, _smb_remote_root


class _FakeShare:
    def __init__(self, name, is_special=False):
        self.name = name
        self.isSpecial = is_special


def test_filter_browsable_shares_hides_admin_and_special_shares():
    shares = [
        _FakeShare("backups"),
        _FakeShare("media"),
        _FakeShare("ADMIN$", is_special=True),
        _FakeShare("C$", is_special=True),
        _FakeShare("IPC$", is_special=True),
        _FakeShare("print$", is_special=True),
    ]
    assert _filter_browsable_shares(shares) == ["backups", "media"]


def test_filter_browsable_shares_hides_dollar_suffixed_even_if_not_flagged_special():
    shares = [_FakeShare("backups"), _FakeShare("weird$")]
    assert _filter_browsable_shares(shares) == ["backups"]


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
