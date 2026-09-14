# QA Eval Tool

A candidate-screening tool: candidates go through 3 assessment rounds
(manual test case writing, debugging, and prompt-driven test
automation). HR authors each round's scenario, reviews an
LLM-generated "superhuman" reference answer, and publishes it; a
candidate's submission is then scored automatically against that same
reference. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design
and what's built vs. stubbed in this pass.

## Setup (Windows / PowerShell)

```powershell
cd qa-eval-tool
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# then edit .env:
#  - paste your real ANTHROPIC_API_KEY (get one at https://console.anthropic.com/settings/keys)
#  - review/change the seeded HR_EMAIL/HR_PASSWORD and CANDIDATE1-3/5_EMAIL/PASSWORD

cd backend
python -m app.seed   # creates the 1 HR + 4 candidate accounts (idempotent)
```

## Setup (macOS / Linux)

```bash
cd qa-eval-tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env as above

cd backend
python -m app.seed
```

Windows users can also just double-click `run_server.bat`, which does
all of the above (venv, deps, `.env`, seeding) and starts the server.

## Run it

```bash
cd backend
uvicorn app.main:app --reload
```

Then open:
- http://127.0.0.1:8000/ — the app itself (HR and candidate views)
- http://127.0.0.1:8000/docs — interactive API docs (try every endpoint here directly, useful while learning how the API behaves)

The SQLite database file (`qa_eval.db`) is created automatically in
`backend/` on first run. Delete it any time to reset all data (you'll
need to re-run `python -m app.seed` afterwards).

## Accounts

There's no signup — this is a screening tool with a fixed roster: 1 HR
account and 4 candidate accounts (candidate1/2/3/5 - all in the 0-7yrs
band; experience band is a hidden feature right now, see app.js),
all created by `python -m app.seed` from the emails/passwords in
your `.env`. Log in with whichever one you're testing as.

## Trying it end-to-end

1. Log in as HR (the `HR_EMAIL`/`HR_PASSWORD` from your `.env`).
2. Create a Round 1 scenario — round "1", band "0-7", title "Login
   form", description "A standard email+password login form with a
   'forgot password' link and a 5-attempt lockout.", a time limit.
   Creating it calls Claude once to generate a reference set of test
   cases — review the table that appears.
3. Optionally hit "Regenerate reference" or hand-edit the reference
   JSON, then click **Publish**. Only a published scenario is visible
   to candidates, and only one scenario per (round, band) can be
   published at a time.
4. Log in as candidate 1 (`CANDIDATE1_EMAIL`/`CANDIDATE1_PASSWORD`,
   0-7yrs band). Click **Start** — this starts the countdown timer.
   Add a few structured test case rows (title/preconditions/steps/
   expected result/priority/type) and **Submit** before time runs out
   (or let it auto-submit at zero).
5. Wait ~10-20 seconds (scoring calls Claude twice in the background:
   once to score, reusing the reference generated in step 2), then
   check "My results" — your score, coverage, and feedback appear
   there.
6. Back in HR, the **Candidates** table shows candidate 1's Round 1 as
   scored with a final score; click **View** for the full drill-down
   (candidate's test cases side-by-side with the reference).

## Running tests

```bash
cd backend
pytest ../tests
```

## What's next

All three rounds now have a full authoring→publish→timed-submit→scoring
pipeline, including Round 3's conversational prompt-driven automation —
the candidate directs a deliberately-imperfect assistant through a chat
per self-titled test case, an execution trace is simulated in the same
call, and one holistic score is generated from the full transcript. See
ARCHITECTURE.md's "Round mechanics" section for the full shape of all
three rounds.

Other things worth tackling as this grows past POC stage: Alembic
migrations instead of the current stack of manual, order-dependent
`migrate_*.py` scripts under `backend/app/`, autosaving a candidate's
in-progress rows on rounds 1/2 the way Round 3's test cases already
autosave a draft (so a page refresh mid-round doesn't lose
typed-but-unsubmitted work), and — once real concurrent HR/candidate
usage shows up — swapping `DATABASE_URL` to Postgres (no code changes
needed elsewhere, see ARCHITECTURE.md).
