"""
FastAPI dependencies for auth: `get_current_user`, plus role-gated
variants. Route functions declare `user: User = Depends(require_hr)`
and FastAPI handles the 401/403 plumbing for you.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, Role
from .security import decode_access_token

# tokenUrl is just for the /docs UI's "Authorize" button; the real
# login endpoint is /auth/login (JSON, not form-encoded).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    return user


def require_hr(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.hr:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "HR access required")
    return user


def require_candidate(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.candidate:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Candidate access required")
    return user
