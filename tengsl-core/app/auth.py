"""Аутентификация, сессии, RBAC и пароли TENGSL."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

PASSWORD_PREFIX = "scrypt"
SESSION_TTL_HOURS = 12


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"{PASSWORD_PREFIX}$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        prefix, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if prefix != PASSWORD_PREFIX:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)


def default_admin_credentials() -> tuple[str, str]:
    username = os.getenv("TENGSL_ADMIN_USERNAME", "admin")
    password = os.getenv("TENGSL_ADMIN_PASSWORD", "password")
    return username, password
