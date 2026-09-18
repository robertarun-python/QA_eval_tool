# Round 2 ↔ Round 4 Swap — Migration Record

**Date:** 2026-09-18
**Predecessor:** `2026-08-23-round3-to-round4-renumber.md`

## What changed

| Slot | Before | After |
|------|--------|-------|
| R1 | Manual test case design | Manual test case design *(unchanged)* |
| R2 | Debugging | **AI-Assisted Test Automation** |
| R3 | AI-prompted coding | AI-prompted coding *(unchanged)* |
| R4 | Automation (conversational / pilot / AI-assisted) | **Debugging** |

This is a **swap**, not a move. Unlike the predecessor plan — which vacated
round 3 and therefore carried a hard warning that `_require_round_unlocked`
would make round 4 permanently unreachable — every slot 1–4 still has
content here, so the gate stays satisfiable and no round is stranded.

## Approach: slot semantics, not identifier rename

The round number plays two distinct roles in this codebase:

1. **Slot / sequence position** — `Scenario.round_number`,
   `Submission.round_number`, `_require_round_unlocked`, `_SCORERS`,
   `ROUND_LABELS`, candidate route paths, nav, report dispatch,
   per-slot pass scores. **These were swapped.**
2. **Content-identity naming** — `Round4TestCase`, `round4_test_cases`,
   `score_round2_investigation`, `round4_*.txt` prompts, `/round4-config`
   HR routes, `.round4-*` CSS. **These were left alone**, as historical
   labels naming the round each flow was built under.

Role 2 was deliberately not renamed. A circular 2↔4 rename would touch
~475 `round4*` occurrences against ~34 `round2*` in backend Python alone,
plus frontend and tests — a volume at which a half-applied rename is a
far likelier outcome than a clean one, and one that changes no behaviour.
This repo already set exactly this precedent: `migrate_round3_freeform.py`
and `migrate_round3_code_cache.py` were explicitly kept under their old
round numbers by the predecessor plan, "a historical record of migrations
that ran when this round really was numbered 3".

**The single authority for which slot runs which flow is
`scoring_service._SCORERS`.** Read it first when the naming confuses you.

## Files changed

- `backend/app/routers/candidate.py` — route paths (`/round/4/*` →
  `/round/2/*` for the automation family; `/round/2/submit` →
  `/round/4/submit` for debugging), `_require_round_unlocked` /
  `_live_scenario` arguments, the explicit `round_number=` on debugging's
  submission creation, candidate-visible prose.
- `backend/app/routers/hr.py` — `ROUND_LABELS`, reference-generation
  branch, publish gates, round-config scoping, report shaping.
- `backend/app/services/scoring_service.py` — `_SCORERS`, and the two
  auto-close/expiry branches that set debugging's investigation-shaped
  content default.
- `backend/app/static/app.js` — `ROUND_LABELS`, candidate API paths, slot
  dispatch for views/reports/briefings, HR settings-card slot,
  candidate-visible headings and buttons.
- `backend/app/migrate_round2_round4_swap.py` — **new**, the data migration.
- `tests/` — paths, payloads, per-slot assertions, and a new
  `_seed_completed_rounds` helper.

## Data migration

`migrate_round2_round4_swap.py` swaps `round_number` 2↔4 in `scenarios`
and `submissions` via a negative temp slot, and swaps the *values* of
`app_settings.round2_passing_score` / `round4_passing_score` (the column
names are slot-keyed and stay; a threshold HR calibrated for debugging
should keep applying to debugging at its new slot).

The swap is self-inverse, so "already applied" cannot be inferred from
the data. A `schema_migrations` marker row guards re-runs; `--force`
overrides.

## Decided: the automation round is Python-only

The automation round was originally built to inherit the language the
candidate picked in the coding round, which used to run before it. After
this swap coding runs *after* automation, so a new candidate has no such
choice on file.

**Decision (pilot): round 2 is Python-only for new candidates.** It
measures automation/QA thinking rather than language choice, and a
language picker is complexity a 30-minute round doesn't need. No
multi-language support is to be added without revisiting this.

`_round3_language_for` keeps its round 3 lookup purely for
legacy/re-entry compatibility — a candidate whose round 3 predates the
renumbering, or who re-enters with one on file, keeps the language they
chose rather than being silently switched. Both behaviours are pinned by
`test_new_candidate_always_gets_python` and
`test_language_still_honours_an_existing_round3_submission`.

## Candidate progress

A submission carries its own `round_number`, so it moves with its content
and stays attached to the same candidate and scenario. Nothing is deleted
or re-scored. The one real behavioural consequence: **debugging now sits
behind rounds 1–3 instead of behind round 1 alone.** A candidate who had
completed only rounds 1–2 under the old numbering now holds a completed
round 1 plus a completed round 2 (their automation work, renumbered) and
continues into round 3 — still contiguous, no gap, nothing unreachable.
