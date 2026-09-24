# Making a change

1. **One branch per change.** `git switch -c fix/<short-name>` from an
   up-to-date `main`. Direct commits on `main` are refused by
   `.githooks/pre-commit` (enable once per clone:
   `git config core.hooksPath .githooks`).
2. **Run the checks.** `scripts/check.sh` - lint, the full offline test
   suite (types, page/API contract, fake-AI browser tests) and the stored
   content audit. No AI/API calls; live replay tests stay skipped.
3. **Merge** into `main` only when the checks pass, then push.

## The real database

- `backend/qa_eval.db` is real candidate data. Tests never open it: they
  run on an in-memory database (`tests/conftest.py`, guarded by
  `tests/test_db_isolation.py`); browser tests use a throwaway file.
- A relative `DATABASE_URL` (`sqlite:///./qa_eval.db`) always means
  `backend/`, whatever directory a command runs from.
- Before changing it by hand (migration, candidate reset, data fix):
  `cd backend && python -m app.backup_db <label>`.
