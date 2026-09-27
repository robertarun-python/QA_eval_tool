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

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved relative to this file, not the process's cwd - the documented
# run command is `cd backend && uvicorn ...`, so a plain "./.env" would
# silently look for (and never find) backend/.env instead of the real
# one at the project root, and pydantic-settings treats a missing env
# file as "no overrides" rather than an error.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SQLITE_PREFIX = "sqlite:///"


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

    @field_validator("database_url")
    @classmethod
    def _pin_relative_sqlite_path(cls, url: str) -> str:
        """A relative SQLite path ("./qa_eval.db") means backend/, the
        documented run directory - not whatever directory a command happens
        to run from. Left relative, a script or test run from the project
        root silently opened (and created) a second, empty qa_eval.db there
        instead of the real one."""
        if url.startswith(_SQLITE_PREFIX) and not url.startswith(_SQLITE_PREFIX + "/") and ":memory:" not in url:
            return _SQLITE_PREFIX + str((_BACKEND_DIR / url[len(_SQLITE_PREFIX):]).resolve())
        return url
    claude_model: str = "claude-sonnet-4-5"
    # Code-writing AI calls answer through a tool call (llm_service._call_claude_tool)
    # instead of JSON text, so whole code files never need JSON escaping - the
    # cause of live "Expecting ',' delimiter" failures. Validated against the
    # real API on 2026-09-24 (every code-writing call type); turn on with
    # LLM_TOOL_OUTPUT=true. If the API rejects it, calls fall back to the
    # JSON-text path automatically.
    llm_tool_output: bool = False
    # Every AI call returns a scripted reply (services/fake_llm.py) - for manual
    # walkthroughs and browser tests without API credit. Never for real
    # candidates: the app shows a banner on every page while it's on.
    llm_fake_mode: bool = False
    # Every AI call is also appended here as one JSON line (metadata and cost,
    # never prompts or replies) - the record behind HR's AI health totals,
    # kept across restarts. See llm_service._append_call_log. Blank = off.
    ai_call_log_path: str = str(_BACKEND_DIR / "ai_calls.jsonl")
    # The monthly AI spending limit (US$) until HR sets one on the AI health
    # card (kept in ai_budget.json next to the call record). See
    # llm_service.check_budget.
    ai_monthly_limit_usd: float = 10.0
    # For test and measurement runs only - both off for the live tool, where
    # HR's "build again" must really ask again and nobody should wait:
    # ai_reuse_replies answers a request identical to an earlier one from the
    # saved reply for free (ai_replies/ next to the call record);
    # ai_batch_jobs sends each call through the Message Batches API at half
    # price, answered within minutes to an hour. See llm_service._send.
    ai_reuse_replies: bool = False
    ai_batch_jobs: bool = False
    ai_batch_max_wait_seconds: int = 7200

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
    # Candidate code runs inside an OS sandbox (execution_service._sandboxed):
    # "auto" uses the platform's (macOS sandbox-exec) and refuses to run code
    # where there is none. "off" runs it unprotected - development only.
    execution_sandbox: str = "auto"
    # A Round 2 practice Run (practice_engine.practice_run): the candidate's
    # Selenium / API / database test against the practice app - several page
    # loads in a real browser, so longer than execution_timeout_seconds.
    practice_run_timeout_seconds: int = 60
    # The Selenium Grid the practice Runs' browsers come from (started on
    # demand, this machine only) and where its tools live (tools/setup_vendor.sh).
    selenium_grid_port: int = 4444
    vendor_dir: str = str(Path(__file__).resolve().parents[2] / "vendor")

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
