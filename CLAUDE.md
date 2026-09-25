# Working on this project

The owner is not a developer and pays for every real AI call. These rules exist
because they were broken once and it cost time and money (2026-09-25).

## Money
- Never spend on the real AI (a practice-app build, a live replay or calibration
  run, anything with `RUN_LLM_REPLAY=1` or a real key) without first stating the
  estimated cost and getting an explicit yes. A practice-app build is about
  $1.50-2 (about $0.30-0.50 when it reuses a working Python app).
- Never ask the owner to "try again" with a paid build to find out whether a fix
  works. Verify it offline first; if a paid check is still needed, run it
  yourself after approval and report the measured result.

## When something fails
- After the same thing fails a second time, stop patching. Say plainly whether
  the approach itself is reliable, and measure it before claiming it is fixed.
- The Round 2 practice-app factory (`backend/app/services/practice_app/`) is
  AI-generated and differs every run: a fix is only "done" when its success
  rate over several builds is measured, not after one good run.
- Report progress as measured numbers ("5 of 6 builds ready"), never "should
  work now".

## The running server
- The owner's server runs `uvicorn --reload`: changing a backend `.py` file
  restarts it, and a restart waits for any running practice-app build, freezing
  the whole site until it finishes. Before touching backend code, check no
  scenario's `config_json.practice_app.status` is `"building"`.
- Never switch branches in the served working tree (it rewrites files). Commit
  on a branch created with `git switch -c`, then merge with
  `git commit-tree "<branch>^{tree}" -p main -p <branch>` +
  `git update-ref refs/heads/main <sha>` + `git switch main`. Work on anything
  large in a separate `git worktree` outside the project folder.

## Tests
- Offline suite (no AI calls): `.venv/bin/python -m pytest -q tests` - also runs
  on GitHub for every push (`.github/workflows/tests.yml`).
- Every fix gets a test that fails without it.
