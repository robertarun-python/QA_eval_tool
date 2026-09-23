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
    # The login cookie's Secure attribute (see routers/auth.py) - False
    # for local dev over plain http://127.0.0.1:8000 (the documented
    # setup), since a Secure cookie is dropped by the browser over a
    # non-https connection. Set true via .env for any real deployment,
    # which should be running behind https.
    cookie_secure: bool = False

    database_url: str = "sqlite:///./qa_eval.db"
    claude_model: str = "claude-sonnet-4-5"

    # The original round 2 automation mode (a round-2 scenario with no
    # config "mode"): the model role-plays running the candidate's test and
    # invents the results, so no result in it comes from real execution.
    # Retired Sep 2026 in favour of AI-Assisted Test Automation, which runs
    # real code - HR can no longer publish or go live with such a scenario.
    # Existing records stay viewable. Left switchable only because the test
    # suite still exercises the legacy flow.
    legacy_simulated_round2_enabled: bool = False

    # Seed-default only now, not read anywhere at request time: the
    # migration (migrate_bulk_candidates.py) uses this once to populate
    # the initial models.AppSettings row's per-round/final passing
    # scores. After that, HR edits the real, runtime-effective values
    # through the Settings page (GET/PUT /hr/settings) - deliberately
    # moved out of here because per-round thresholds need to be
    # self-serve changeable by HR without a server restart, which a
    # .env value can never be.
    passing_score: int = 70

    # How much clock/network slack a submit gets past a round's own
    # time_limit_minutes before the server rejects it - covers the small
    # gap between the client-side timer hitting zero and the auto-submit
    # request actually landing, not meant to allow real extra time. See
    # routers/candidate.py's _require_within_time_limit.
    submission_grace_seconds: int = 60

    # How often Round 4's assistant is instructed to be correct per turn
    # (the rest of the time it introduces a deliberate flaw) - the whole
    # premise of that round is the candidate catching what the assistant
    # gets wrong. HR can still override this per scenario via
    # Scenario.config_json (see llm_service.DEFAULT_ROUND4_CONFIG); this
    # is only the fallback when a scenario doesn't set its own. Lowered
    # from 60 - HR found candidates were seeing too few catchable
    # mistakes to genuinely exercise the round. Applies to new scenarios
    # only; anything already published keeps whatever config_json it
    # already has.
    round4_default_assistance_pct: int = 50

    # Round 3 (AI-prompted coding) code execution - see
    # services/execution_service.py, which runs candidate code as a local
    # subprocess with this as its wall-clock timeout. Used for the
    # BATCH runs only (scoring_service, against HR's fixed reference
    # test cases) - short on purpose, since nothing there is waiting on
    # a human to type.
    execution_timeout_seconds: int = 10
    # The candidate's own interactive "Run" (see execution_service's
    # InteractiveSession) needs a much longer ceiling than the batch
    # timeout above - a human has to actually read each input() prompt
    # and type a response, which the batch timeout would kill mid-typing.
    # This bounds the OVERALL session lifetime (a safety net against a
    # truly abandoned or hung run, not the per-response wait), separate
    # from and much larger than execution_timeout_seconds.
    interactive_execution_timeout_seconds: int = 300

    # Seeded accounts (see app/seed.py) - this POC uses fixed, pre-provisioned
    # logins instead of open signup: 1 HR + 3 candidates. Emails default to
    # something readable since they're not secret - just identify which
    # account is which. Passwords have no default, same reasoning as
    # jwt_secret_key above: a fallback here would be a fixed, known
    # password sitting in this file (including full HR access), silently
    # active unless .env happens to override it. Required means the app
    # refuses to start rather than seeding a guessable account.
    hr_email: str = "hr@example.com"
    hr_password: str
    candidate1_email: str = "candidate1@example.com"
    candidate1_password: str
    candidate2_email: str = "candidate2@example.com"
    candidate2_password: str
    candidate3_email: str = "candidate3@example.com"
    candidate3_password: str
    candidate5_email: str = "candidate5@example.com"
    candidate5_password: str


# Import this singleton everywhere instead of re-reading env vars.
settings = Settings()
