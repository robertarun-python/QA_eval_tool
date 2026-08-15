"""
Login only. Accounts are seeded (see app/seed.py) - there is no signup
route. This is a screening tool with a fixed roster (1 HR + 3 candidate
test accounts), not a public-signup product.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import LoginRequest, TokenResponse
from ..security import verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        # Deliberately the same error for "no such user" and "wrong
        # password" - don't leak which one it was.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    token = create_access_token(user.id, user.role.value)
    return TokenResponse(access_token=token, role=user.role.value)
