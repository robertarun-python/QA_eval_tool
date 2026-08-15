"""
Central place for reading configuration from environment variables / .env.

Why pydantic-settings: it validates on startup (fail fast if
ANTHROPIC_API_KEY is missing, instead of failing confusingly later mid
API call) and gives you autocomplete for `settings.whatever` everywhere
else in the app.
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
    jwt_secret_key: str = "insecure-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 12  # 12 hour session

    database_url: str = "sqlite:///./qa_eval.db"
    claude_model: str = "claude-sonnet-4-5"

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
