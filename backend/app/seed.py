"""
Creates the fixed, pre-provisioned accounts this tool uses instead of open
signup: 1 HR account + 3 candidate accounts. Idempotent - safe to re-run;
existing accounts are left untouched (edit the row directly in the DB, or
delete qa_eval.db, if you need to change credentials for an account that
already exists).

Experience band is a hidden feature right now (HR no longer picks one -
see app.js) - every seeded candidate gets the same band so all three can
see whatever HR publishes.

Run from backend/: python -m app.seed
"""
from .database import Base, SessionLocal, engine
from .models import User, Role, ExperienceBand
from .security import hash_password
from .config import settings


def seed_users(db) -> list[User]:
    to_create = [
        (settings.hr_email, settings.hr_password, Role.hr, None),
        (settings.candidate1_email, settings.candidate1_password, Role.candidate, ExperienceBand.junior),
        (settings.candidate2_email, settings.candidate2_password, Role.candidate, ExperienceBand.junior),
        (settings.candidate3_email, settings.candidate3_password, Role.candidate, ExperienceBand.junior),
    ]
    created = []
    for email, password, role, band in to_create:
        if db.query(User).filter(User.email == email).first() is not None:
            continue
        user = User(
            email=email,
            password_hash=hash_password(password),
            role=role,
            experience_band=band,
        )
        db.add(user)
        created.append(user)
    if created:
        db.commit()
    return created


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        created = seed_users(db)
        if created:
            print(f"Created {len(created)} account(s):")
            for u in created:
                print(f"  {u.role.value:<10} {u.email}")
        else:
            print("All seeded accounts already exist - nothing to do.")
        print("\nPasswords come from your .env (HR_PASSWORD / CANDIDATE1_PASSWORD / ...).")
    finally:
        db.close()
