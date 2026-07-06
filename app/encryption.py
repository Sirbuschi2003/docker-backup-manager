"""
Encrypts backups at rest with AES-256-CBC + HMAC-SHA256 (encrypt-then-MAC),
streamed in chunks so multi-gigabyte volume archives never have to fit in
memory at once. The master key comes only from the DBM_ENCRYPTION_KEY
environment variable - never stored in the database - so a leaked DB alone
can't decrypt anything.

File format: <16-byte IV> <ciphertext (PKCS7-padded)> <32-byte HMAC tag>
The tag covers IV + ciphertext. On decrypt, the tag is verified in a first
streaming pass before any plaintext is written out.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, hmac, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENCRYPTED_SUFFIX = ".enc"
CHUNK_SIZE = 1024 * 1024
_HKDF_INFO = b"docker-backup-manager-v1"


class DecryptionError(Exception):
    pass


def _master_key() -> Optional[bytes]:
    raw = os.environ.get("DBM_ENCRYPTION_KEY")
    if not raw:
        return None
    try:
        key = base64.urlsafe_b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("DBM_ENCRYPTION_KEY must be valid base64") from exc
    if len(key) < 32:
        raise ValueError("DBM_ENCRYPTION_KEY must decode to at least 32 bytes")
    return key


def is_enabled() -> bool:
    return _master_key() is not None


def _derive_keys(master_key: bytes) -> tuple[bytes, bytes]:
    okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=None, info=_HKDF_INFO).derive(master_key)
    return okm[:32], okm[32:]


def encrypt_file(src: Path, remove_source: bool = True) -> Path:
    """Encrypts src in place, writing `<src>.enc` and removing the plaintext.
    Returns the new encrypted path."""
    master_key = _master_key()
    if master_key is None:
        raise RuntimeError("Encryption requested but DBM_ENCRYPTION_KEY is not set")
    aes_key, hmac_key = _derive_keys(master_key)

    iv = os.urandom(16)
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    padder = padding.PKCS7(128).padder()
    tag = hmac.HMAC(hmac_key, hashes.SHA256())
    tag.update(iv)

    dest = src.with_name(src.name + ENCRYPTED_SUFFIX)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(src.parent), prefix=".dbm-enc-")
    try:
        with os.fdopen(tmp_fd, "wb") as fout, open(src, "rb") as fin:
            fout.write(iv)
            while True:
                chunk = fin.read(CHUNK_SIZE)
                if not chunk:
                    break
                out = encryptor.update(padder.update(chunk))
                fout.write(out)
                tag.update(out)
            out = encryptor.update(padder.finalize()) + encryptor.finalize()
            fout.write(out)
            tag.update(out)
            fout.write(tag.finalize())
        shutil.move(tmp_name, dest)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    if remove_source:
        src.unlink()
    return dest


def decrypt_file(src: Path, dest: Path) -> None:
    """Verifies the HMAC tag in a first streaming pass, then decrypts into
    dest in a second pass. Raises DecryptionError if the key is wrong or the
    file was tampered with."""
    master_key = _master_key()
    if master_key is None:
        raise RuntimeError("Decryption requested but DBM_ENCRYPTION_KEY is not set")
    aes_key, hmac_key = _derive_keys(master_key)

    file_size = src.stat().st_size
    if file_size < 16 + 32:
        raise DecryptionError(f"{src} is too small to be a valid encrypted backup file")
    ciphertext_len = file_size - 16 - 32

    with open(src, "rb") as f:
        iv = f.read(16)
        tag = hmac.HMAC(hmac_key, hashes.SHA256())
        tag.update(iv)
        remaining = ciphertext_len
        while remaining > 0:
            chunk = f.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            tag.update(chunk)
            remaining -= len(chunk)
        expected_tag = f.read(32)
        try:
            tag.verify(expected_tag)
        except Exception as exc:  # noqa: BLE001
            raise DecryptionError(f"Integrity check failed for {src} (wrong key or corrupted file)") from exc

    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    with open(src, "rb") as f, open(dest, "wb") as fout:
        f.read(16)
        remaining = ciphertext_len
        while remaining > 0:
            chunk = f.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            plain = decryptor.update(chunk)
            fout.write(unpadder.update(plain))
        final_plain = decryptor.finalize()
        fout.write(unpadder.update(final_plain))
        fout.write(unpadder.finalize())


def encrypt_directory_in_place(root: Path, on_progress=None) -> None:
    files = [p for p in root.rglob("*") if p.is_file()]
    for idx, path in enumerate(files, start=1):
        if on_progress:
            on_progress(f"Encrypting {path.name}", idx, len(files))
        encrypt_file(path)


def is_backup_encrypted(root: Path) -> bool:
    return any(p.suffix == ENCRYPTED_SUFFIX for p in root.rglob("*") if p.is_file())


def decrypt_directory_to_temp(root: Path) -> tempfile.TemporaryDirectory:
    """Returns a TemporaryDirectory whose contents mirror `root` with every
    `*.enc` file decrypted back to its original name. Caller must use it as a
    context manager (or call .cleanup()) so plaintext never lingers on disk."""
    tmp = tempfile.TemporaryDirectory(prefix="dbm-decrypt-")
    tmp_root = Path(tmp.name)
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if path.suffix == ENCRYPTED_SUFFIX:
            dest = tmp_root / rel.with_suffix("")
            dest.parent.mkdir(parents=True, exist_ok=True)
            decrypt_file(path, dest)
        else:
            dest = tmp_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    return tmp
