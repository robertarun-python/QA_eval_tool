"""
Password hashing and JWT issuing/verification.

Kept deliberately small and separate from routers/models: this is the
one file that touches cryptography, so it's the one file to audit
carefully and the one place to change if you ever swap JWT for
session cookies.
"""
import secrets
from datetime import datetime, timedelta

from jose import jwt, JWTError
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


# A valid bcrypt hash of a password nobody will ever type, computed once
# at import time - see routers/auth.py's login. Without this, "no such
# identifier" and "wrong password for a real account" are distinguishable
# by response time alone: bcrypt verification costs ~150-300ms, and
# `user is None or not verify_password(...)` short-circuits past that
# entirely when there's no user to check against. Always running a real
# verify_password() call, even against this dummy hash, keeps both cases
# taking the same time regardless of which one it actually is.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(32))


def generate_session_id() -> str:
    """A fresh single-active-session id - see models.User.active_session_id.
    Minted once per login and embedded in that login's own token below;
    routers/auth.py's login is responsible for actually persisting it onto
    the user row."""
    return secrets.token_hex(16)


def create_access_token(user_id: int, role: str, session_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expires_minutes)
    payload = {"sub": str(user_id), "role": role, "sid": session_id, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
