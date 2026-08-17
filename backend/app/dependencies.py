"""
FastAPI dependencies for auth: `get_current_user`, plus role-gated
variants. Route functions declare `user: User = Depends(require_hr)`
and FastAPI handles the 401/403 plumbing for you.
"""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, Role
from .security import decode_access_token

COOKIE_NAME = "qa_eval_token"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    # The JWT travels only as an httpOnly cookie now (see routers/auth.py's
    # Set-Cookie on /auth/login) - no Authorization header to read.
    # /docs' "Try it out" still works end-to-end: hitting POST /auth/login
    # from the docs page itself sets this cookie in that same browser
    # session, and subsequent "Try it out" calls send it automatically
    # (same-origin) - the Swagger "Authorize" padlock (built for header
    # tokens) is just unused now, not broken.
    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
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
