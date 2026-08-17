"""
Login, plus a /me to resolve identity from a token. Accounts are seeded
(see app/seed.py) or bulk-uploaded by HR (see routers/hr.py, credential_service.py)
- there is no self-signup route.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import User
from ..schemas import LoginRequest, TokenResponse, MeOut
from ..security import verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    # identifier matches either column: HR/seeded accounts log in with
    # their email, bulk-uploaded candidates with a plain username (their
    # email's local part - see credential_service.derive_username). Most
    # users will only ever have one of the two set, so this is never
    # ambiguous in practice.
    user = db.query(User).filter(
        or_(User.email == payload.identifier, User.username == payload.identifier)
    ).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        # Deliberately the same error for "no such user" and "wrong
        # password" - don't leak which one it was.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or username, or wrong password")

    token = create_access_token(user.id, user.role.value)
    return TokenResponse(access_token=token, role=user.role.value)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    """Resolves identity from the token itself (see MeOut) - the
    frontend calls this once per session load so the sidebar always
    shows the real signed-in account, not a guess."""
    return MeOut(email=user.email, role=user.role.value)
