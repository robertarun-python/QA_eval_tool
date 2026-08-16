"""
THE configurable-parameters file for the backend: every value here that
isn't a plain default (secrets, seeded credentials, scoring thresholds,
timing) is meant to be the one place you change it, via .env - nothing
in routers/services should hardcode a value that belongs here instead.
Values that are legitimately per-scenario (a round's own time_limit_minutes,
its experience_band, ...) live in the database instead, set by HR through
the UI, not here - this file is for things that are the same across
every scenario/candidate, or that vary by deployment rather than by
scenario.

Why pydantic-settings: it validates on startup (fail fast if a required
value like jwt_secret_key is missing, instead of failing confusingly
later mid-request) and gives you autocomplete for `settings.whatever`
everywhere else in the app.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved relative to this file, not the process's cwd - the documented
# run command is `cd backend && uvicorn ...`, so a plain "./.env" would
# silently look for (and never find) backend/.env instead of the real
# one at the project root, and pydantic-settings treats a missing env
# file as "no overrides" rather than an error.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    anthropic_api_key: str = ""
    # No default on purpose: unlike a missing API key (fails loudly the
    # first time it's used), a missing secret here would otherwise fall
    # back to a fixed string sitting in this file - anyone who reads the
    # repo could forge a valid session token (including one claiming to
    # be HR) with no password needed. Required means the app refuses to
    # start at all rather than silently running with a known-weak secret.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 12  # 12 hour session

    database_url: str = "sqlite:///./qa_eval.db"
    claude_model: str = "claude-sonnet-4-5"

    # A submission's final_score at or above this "clears" the round -
    # single source of truth so the History dashboard's cleared/not-cleared
    # split always matches whatever threshold the UI's score-good/score-bad
    # styling implies. Was an implicit 70 hardcoded in the frontend only.
    passing_score: int = 70

    # How much clock/network slack a submit gets past a round's own
    # time_limit_minutes before the server rejects it - covers the small
    # gap between the client-side timer hitting zero and the auto-submit
    # request actually landing, not meant to allow real extra time. See
    # routers/candidate.py's _require_within_time_limit.
    submission_grace_seconds: int = 60

    # How often Round 3's assistant is instructed to be correct per turn
    # (the rest of the time it introduces a deliberate flaw) - the whole
    # premise of that round is the candidate catching what the assistant
    # gets wrong. HR can still override this per scenario via
    # Scenario.config_json (see llm_service.DEFAULT_ROUND3_CONFIG); this
    # is only the fallback when a scenario doesn't set its own.
    round3_default_assistance_pct: int = 60

    # Seeded accounts (see app/seed.py) - this POC uses fixed, pre-provisioned
    # logins instead of open signup: 1 HR + 3 candidates (2x 0-7yrs, 1x 7+yrs).
    hr_email: str = "hr@example.com"
    hr_password: str = "hr-password-change-me"
    candidate1_email: str = "candidate1@example.com"
    candidate1_password: str = "candidate1-password-change-me"
    candidate2_email: str = "candidate2@example.com"
    candidate2_password: str = "candidate2-password-change-me"
    candidate3_email: str = "candidate3@example.com"
    candidate3_password: str = "candidate3-password-change-me"


# Import this singleton everywhere instead of re-reading env vars.
settings = Settings()
