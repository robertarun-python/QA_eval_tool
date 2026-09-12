"""
Login, plus a /me to resolve identity from a token. Accounts are seeded
(see app/seed.py) or bulk-uploaded by HR (see routers/hr.py, credential_service.py)
- there is no self-signup route.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..dependencies import COOKIE_NAME, get_current_user
from ..models import User
from ..schemas import LoginRequest, LoginResponse, MeOut
from ..security import verify_password, create_access_token, DUMMY_PASSWORD_HASH

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    # identifier matches either column: HR/seeded accounts log in with
    # their email, bulk-uploaded candidates with a plain username (their
    # email's local part - see credential_service.derive_username). Most
    # users will only ever have one of the two set, so this is never
    # ambiguous in practice.
    user = db.query(User).filter(
        or_(User.email == payload.identifier, User.username == payload.identifier)
    ).first()
    # Always runs a real bcrypt verify, even with no user to check against
    # (see security.DUMMY_PASSWORD_HASH) - `or`'s short-circuit would
    # otherwise skip that ~150-300ms cost whenever the identifier doesn't
    # exist, making "no such user" and "wrong password" distinguishable
    # by response time alone.
    password_ok = verify_password(payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not password_ok:
        # Deliberately the same error for "no such user" and "wrong
        # password" - don't leak which one it was.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or username, or wrong password")

    token = create_access_token(user.id, user.role.value)
    # httpOnly: JS can never read this, even via an XSS injection - the
    # old approach (returning the token in the JSON body, stored in
    # sessionStorage) was readable by any injected script, same as
    # localStorage would be. samesite="strict": this app is entirely
    # same-origin (Jinja2 + static mounted by the same FastAPI app, no
    # CORS config anywhere) with no cross-site embedding use case, so this
    # alone is sufficient CSRF protection - the cookie is simply never
    # sent on a cross-site request at all, without needing a separate
    # CSRF-token scheme on top.
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True, samesite="strict", secure=settings.cookie_secure,
        max_age=settings.jwt_expires_minutes * 60,
    )
    return LoginResponse(role=user.role.value)


@router.post("/logout", status_code=204)
def logout(response: Response):
    """Clears the login cookie server-side - JS can't delete an httpOnly
    cookie itself, so this has to be a real request, not just clearing
    local state (which is all the old sessionStorage-based logout did)."""
    response.delete_cookie(COOKIE_NAME)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    """Resolves identity from the token itself (see MeOut) - the
    frontend calls this once per session load so the sidebar always
    shows the real signed-in account, not a guess. Also doubles as the
    "am I logged in" check on page load now that the cookie is httpOnly
    and JS can't just read it directly to decide that itself."""
    return MeOut(email=user.email, role=user.role.value)
