"""
Login, plus a /me to resolve identity from a token. Accounts are seeded
(see app/seed.py) or bulk-uploaded by HR (see routers/hr.py, credential_service.py)
- there is no self-signup route.
"""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..dependencies import COOKIE_NAME, get_current_user
from ..models import User, Role, Submission, RoundStatus
from ..schemas import LoginRequest, LoginResponse, MeOut
from ..security import verify_password, create_access_token, decode_access_token, generate_session_id, DUMMY_PASSWORD_HASH
from ..services.scoring_service import finalize_abandoned_submission, score_submission_in_background

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

    # A fresh session id on every login (see models.User.active_session_id) -
    # this is what lets dependencies.get_current_user notice a second,
    # later login elsewhere and kick out whichever session is still
    # holding the previous one, but only once that matters (a round is
    # actually in_progress). Every login overwrites this unconditionally,
    # including HR's and a candidate's between rounds - there's no
    # server-side cost to that, and it keeps the rule simple: whichever
    # session logged in most recently is always the "real" one, ready the
    # moment a round starts.
    session_id = generate_session_id()
    user.active_session_id = session_id
    db.commit()

    token = create_access_token(user.id, user.role.value, session_id)
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
def logout(request: Request, response: Response, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Clears the login cookie server-side - JS can't delete an httpOnly
    cookie itself, so this has to be a real request, not just clearing
    local state (which is all the old sessionStorage-based logout did).

    Also immediately ends whatever round the candidate has in progress
    right now, if any - logging out is no longer a free pause. Whatever
    was completed (or left blank) gets assessed as of this exact moment,
    the same outcome a genuine timeout would produce (see
    scoring_service.finalize_abandoned_submission) - "didn't attempt it"
    scores zero, it isn't a way to avoid a bad answer. app.js's logout()
    warns the candidate about exactly this before ever calling here.

    Deliberately does its own best-effort identity check rather than
    requiring Depends(get_current_user): logout has to succeed and clear
    the cookie even when the token is already missing or expired (the
    frontend calls this unconditionally, including as a 401 fallback -
    see api()'s own comment) - there's just nothing to finalize in that
    case."""
    response.delete_cookie(COOKIE_NAME)

    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        return
    payload = decode_access_token(token)
    if payload is None:
        return
    user = db.get(User, int(payload["sub"]))
    if user is None:
        return
    # Best-effort hygiene, not what actually enforces anything (a later
    # login already overwrites this regardless) - just avoids a session id
    # lingering as "active" once its own cookie is gone.
    user.active_session_id = None
    db.commit()
    if user.role != Role.candidate:
        return

    in_progress = db.query(Submission).filter(
        Submission.user_id == user.id, Submission.archived.is_(False), Submission.status == RoundStatus.in_progress,
    ).all()
    if not in_progress:
        return
    now = datetime.utcnow()
    for submission in in_progress:
        finalize_abandoned_submission(submission, "Candidate logged out before completing this round", now)
    db.commit()
    for submission in in_progress:
        background_tasks.add_task(score_submission_in_background, submission.id)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    """Resolves identity from the token itself (see MeOut) - the
    frontend calls this once per session load so the sidebar always
    shows the real signed-in account, not a guess. Also doubles as the
    "am I logged in" check on page load now that the cookie is httpOnly
    and JS can't just read it directly to decide that itself."""
    return MeOut(email=user.email, role=user.role.value)
