"""
HR's bulk candidate upload (see routers/hr.py's POST /candidates/upload):
parsing both accepted file formats into plain (row_number, email, exam_date)
rows, then turning each row into a created or reset User + CandidateAppearance.

Split out from hr.py (which stays HTTP-routing/dependency-wiring focused)
the same way llm_service.py/scoring_service.py already are - this is
substantial enough business logic to deserve its own module and its own
focused tests.
"""
import csv
import io
from datetime import datetime

import openpyxl
from email_validator import validate_email, EmailNotValidError
from sqlalchemy.orm import Session

from ..credential_service import derive_username, derive_password
from ..models import User, Role, Submission, CandidateAppearance, AppSettings
from ..schemas import BulkUploadRowResult, BulkUploadResult
from ..security import hash_password

_DATE_FORMAT = "%Y-%m-%d"


def _looks_like_email(value: str) -> bool:
    return "@" in value


def parse_upload_rows(filename: str, content: bytes) -> list[tuple[int, str, str]]:
    """Returns (row_number, raw_email, raw_exam_date) tuples, 1-indexed
    by their position among DATA rows (a detected header row doesn't
    count). Row-level validation (is the email actually valid, does the
    date actually parse) happens later in process_upload_rows - this
    function's only job is figuring out which cells are which."""
    if filename.lower().endswith(".xlsx"):
        return _parse_xlsx(content)
    return _parse_txt(content)


def _parse_txt(content: bytes) -> list[tuple[int, str, str]]:
    text = content.decode("utf-8-sig")  # -sig strips a BOM if present, harmless otherwise
    rows = [r for r in csv.reader(io.StringIO(text)) if r and any(cell.strip() for cell in r)]
    if rows and not _looks_like_email(rows[0][0].strip()):
        rows = rows[1:]  # first cell isn't email-shaped - treat it as a header row, skip it
    return [
        (i + 1, row[0].strip(), row[1].strip() if len(row) > 1 else "")
        for i, row in enumerate(rows)
    ]


def _parse_xlsx(content: bytes) -> list[tuple[int, str, str]]:
    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.active
    all_rows = [row for row in sheet.iter_rows(values_only=True) if any(cell is not None for cell in row)]
    if not all_rows:
        return []

    email_idx, date_idx = 0, 1
    first_cell = str(all_rows[0][0]).strip() if all_rows[0][0] is not None else ""
    header = [str(c).strip().lower() if c is not None else "" for c in all_rows[0]]
    if "email" in header and "exam_date" in header:
        email_idx, date_idx = header.index("email"), header.index("exam_date")
        data_rows = all_rows[1:]
    elif _looks_like_email(first_cell):
        data_rows = all_rows  # no header - first row is already data
    else:
        data_rows = all_rows[1:]  # unrecognized header text - assume it's a header anyway, skip it

    out = []
    for i, row in enumerate(data_rows):
        email_cell = row[email_idx] if email_idx < len(row) else None
        date_cell = row[date_idx] if date_idx < len(row) else None
        if email_cell is None:
            continue
        # openpyxl returns a real datetime for a native Excel date cell,
        # not a string - normalize both cases to the same "YYYY-MM-DD" text
        # so the rest of the pipeline only ever deals with strings.
        if isinstance(date_cell, datetime):
            date_str = date_cell.strftime(_DATE_FORMAT)
        else:
            date_str = str(date_cell).strip() if date_cell is not None else ""
        out.append((i + 1, str(email_cell).strip(), date_str))
    return out


def _validate_email(raw: str) -> str:
    if not raw:
        raise ValueError("Missing email")
    try:
        return validate_email(raw, check_deliverability=False).normalized
    except EmailNotValidError as e:
        raise ValueError(f"Invalid email: {e}")


def _parse_exam_date(raw: str) -> datetime:
    if not raw:
        raise ValueError("Missing exam date")
    try:
        return datetime.strptime(raw, _DATE_FORMAT)
    except ValueError:
        raise ValueError(f"Invalid exam date '{raw}' - expected YYYY-MM-DD")


def process_upload_rows(
    db: Session, rows: list[tuple[int, str, str]], app_settings: AppSettings,
) -> BulkUploadResult:
    seen_emails: set[str] = set()
    results: list[BulkUploadRowResult] = []
    created_count = reset_count = error_count = 0

    for row_number, raw_email, raw_date in rows:
        try:
            email = _validate_email(raw_email)
            exam_date = _parse_exam_date(raw_date)
            if email.lower() in seen_emails:
                raise ValueError("Duplicate email within this file")
            seen_emails.add(email.lower())

            username = derive_username(email)
            existing_user = db.query(User).filter(User.email == email).first()

            if existing_user is None:
                # A DIFFERENT existing user already has this derived
                # username - reject rather than silently auto-suffixing,
                # since the whole point of the scheme is a predictable,
                # communicable login (see credential_service.py).
                collision = db.query(User).filter(User.username == username).first()
                if collision is not None:
                    raise ValueError(f"Username '{username}' is already in use by a different email")

                user = User(
                    email=email, username=username,
                    password_hash=hash_password(derive_password(email)),
                    role=Role.candidate,
                )
                db.add(user)
                db.flush()  # need user.id for the appearance row below
                db.add(CandidateAppearance(user_id=user.id, email=email, exam_date=exam_date, is_current=True))
                db.commit()
                created_count += 1
                results.append(BulkUploadRowResult(row_number=row_number, email=email, status="created", username=username))
            else:
                _reset_and_archive(db, existing_user, email, exam_date, app_settings)
                db.commit()
                reset_count += 1
                results.append(BulkUploadRowResult(
                    row_number=row_number, email=email, status="reset",
                    username=existing_user.username or username,
                ))
        except ValueError as e:
            db.rollback()
            error_count += 1
            results.append(BulkUploadRowResult(row_number=row_number, email=raw_email or None, status="error", error=str(e)))

    return BulkUploadResult(
        created_count=created_count, reset_count=reset_count, error_count=error_count, rows=results,
    )


def _reset_and_archive(db: Session, user: User, email: str, exam_date: datetime, app_settings: AppSettings) -> bool:
    """A candidate re-applying: archive every one of their current
    submissions (so a fresh attempt doesn't collide with the old one in
    the many "one submission per (user, scenario)" lookups across
    candidate.py - see Submission.archived), flip their previous
    appearance to non-current, and record a new current one. username/
    password/band are left exactly as they were - same login, same band,
    carried forward. Returns whether this appearance falls within the
    configured re-application window of the previous one."""
    if user.username is None:
        user.username = derive_username(email)  # backfill - e.g. this account predates the upload feature

    previous = next((a for a in user.appearances if a.is_current), None)
    reapplied_within_window = False
    if previous is not None:
        months_elapsed = (exam_date - previous.exam_date).days / 30.44
        reapplied_within_window = months_elapsed < app_settings.reapplication_window_months
        previous.is_current = False

    db.query(Submission).filter(
        Submission.user_id == user.id, Submission.archived.is_(False),
    ).update({"archived": True})

    db.add(CandidateAppearance(
        user_id=user.id, email=email, exam_date=exam_date,
        is_current=True, reapplied_within_window=reapplied_within_window,
    ))
    return reapplied_within_window
