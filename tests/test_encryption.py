import base64
import os
import secrets
from pathlib import Path

import pytest

from app import encryption


@pytest.fixture
def enable_encryption(monkeypatch):
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("DBM_ENCRYPTION_KEY", key)
    yield key


def test_is_enabled_false_without_key(monkeypatch):
    monkeypatch.delenv("DBM_ENCRYPTION_KEY", raising=False)
    assert encryption.is_enabled() is False


def test_is_enabled_true_with_key(enable_encryption):
    assert encryption.is_enabled() is True


def test_is_enabled_false_with_malformed_key_instead_of_raising(monkeypatch):
    # A user pasting a bad/illustrative example value (not real base64, or too
    # short) must not crash every page that checks encryption status.
    monkeypatch.setenv("DBM_ENCRYPTION_KEY", "not-valid-base64!!")
    assert encryption.is_enabled() is False


def test_config_error_none_when_unset(monkeypatch):
    monkeypatch.delenv("DBM_ENCRYPTION_KEY", raising=False)
    assert encryption.config_error() is None


def test_config_error_none_when_valid(enable_encryption):
    assert encryption.config_error() is None


def test_config_error_reports_invalid_base64(monkeypatch):
    # Malformed padding/length - the same kind of error a hand-typed or
    # mistyped example key produces (real incident: a user pasted an
    # illustrative example key verbatim and every page started 500ing).
    monkeypatch.setenv("DBM_ENCRYPTION_KEY", "Xk9mQwZ3vJf456755gfcp2LrN8hYtF6cEbA1dWs5oGiU7xR4nKvHc=")
    assert "base64" in encryption.config_error()


def test_config_error_reports_too_short_key(monkeypatch):
    monkeypatch.setenv("DBM_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"short").decode())
    assert "32 bytes" in encryption.config_error()


def test_encrypt_decrypt_roundtrip(enable_encryption, tmp_path: Path):
    src = tmp_path / "data.bin"
    original = os.urandom(5000)  # a few chunks worth
    src.write_bytes(original)

    enc_path = encryption.encrypt_file(src)
    assert enc_path.name == "data.bin.enc"
    assert not src.exists()

    dest = tmp_path / "restored.bin"
    encryption.decrypt_file(enc_path, dest)
    assert dest.read_bytes() == original


def test_encrypt_decrypt_empty_file(enable_encryption, tmp_path: Path):
    src = tmp_path / "empty.txt"
    src.write_bytes(b"")
    enc_path = encryption.encrypt_file(src)
    dest = tmp_path / "out.txt"
    encryption.decrypt_file(enc_path, dest)
    assert dest.read_bytes() == b""


def test_tampered_file_fails_integrity_check(enable_encryption, tmp_path: Path):
    src = tmp_path / "data.bin"
    src.write_bytes(b"some secret backup content")
    enc_path = encryption.encrypt_file(src)

    data = bytearray(enc_path.read_bytes())
    data[20] ^= 0xFF  # flip a bit in the ciphertext
    enc_path.write_bytes(bytes(data))

    with pytest.raises(encryption.DecryptionError):
        encryption.decrypt_file(enc_path, tmp_path / "out.bin")


def test_wrong_key_fails_integrity_check(tmp_path: Path, monkeypatch):
    key1 = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("DBM_ENCRYPTION_KEY", key1)
    src = tmp_path / "data.bin"
    src.write_bytes(b"secret")
    enc_path = encryption.encrypt_file(src)

    key2 = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("DBM_ENCRYPTION_KEY", key2)
    with pytest.raises(encryption.DecryptionError):
        encryption.decrypt_file(enc_path, tmp_path / "out.bin")


def test_encrypt_directory_in_place_and_detect(enable_encryption, tmp_path: Path):
    root = tmp_path / "backup" / "v1"
    root.mkdir(parents=True)
    (root / "meta.json").write_text('{"a": 1}')
    sub = root / "volumes"
    sub.mkdir()
    (sub / "vol.tar.gz").write_bytes(os.urandom(2000))

    assert encryption.is_backup_encrypted(root) is False
    encryption.encrypt_directory_in_place(root)
    assert encryption.is_backup_encrypted(root) is True
    assert (root / "meta.json.enc").exists()
    assert (sub / "vol.tar.gz.enc").exists()
    assert not (root / "meta.json").exists()

    with encryption.decrypt_directory_to_temp(root) as tmp_dir:
        tmp_root = Path(tmp_dir)
        assert (tmp_root / "meta.json").read_text() == '{"a": 1}'
        assert (tmp_root / "volumes" / "vol.tar.gz").exists()
