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

    # Single-active-session enforcement (see models.User.active_session_id) -
    # only actually matters while this candidate has a round genuinely
    # in_progress; outside of an active round there's nothing at stake, so
    # an older, still-technically-valid session elsewhere is left alone
    # rather than forcing a surprise logout for no reason. `!=` rather than
    # an explicit both-None check: a token minted before this feature
    # existed has no "sid" claim (None), and a fresh account's
    # active_session_id starts NULL too - both None compares equal, so an
    # old token is never retroactively rejected just for predating this
    # column; only a REAL mismatch (a later login elsewhere actually
    # overwrote the row) triggers this.
    # Always, not only during a round (journey matrix, 2026-09-27): a logged-out
    # or replaced session - an old tab whose timer fires, a second device - could
    # still start and submit a round outside one. Safe to be strict now: such a
    # session's own automatic logout no longer touches the current session
    # (routers/auth.logout).
    if user.role == Role.candidate and payload.get("sid") != user.active_session_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "You've been logged in from another device or browser - this session is no longer active.",
        )
    return user


def require_hr(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.hr:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "HR access required")
    return user


def require_candidate(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.candidate:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Candidate access required")
    return user
