"""Código de reclamo, contraseña de admin y tokens de acceso."""
import base64
import binascii
import hmac
import os
import secrets as _secrets
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD = 12
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_hasher = PasswordHasher()


def new_setup_code() -> str:
    raw = "".join(_secrets.choice(_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def ensure_setup_code(path: Path) -> str:
    if path.exists():
        return path.read_text().strip()
    code = new_setup_code()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(code)
    return code


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str | None, password: str) -> bool:
    if not hashed:
        return False
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return _secrets.token_urlsafe(32)


def same(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode(), b.encode())


def check_bearer(header: str | None, expected: str | None) -> bool:
    if not header or not header.startswith("Bearer "):
        return False
    return same(header[7:].strip(), expected)


def check_basic(header: str | None, expected: str | None) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, _, password = decoded.partition(":")
    return same(password, expected)
