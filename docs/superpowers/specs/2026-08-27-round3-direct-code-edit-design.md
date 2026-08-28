# Round 3: Direct Code Edit — Design

Addendum to `docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md` (Round
3's overall shape) and `docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md`
(the deterministic construct-checklist engine). That second spec explicitly
deferred this exact feature under "Explicitly out of scope, deferred to a
follow-up": *"Candidate-pasted code, syntax-only correction... a distinct
interaction mode... reusable `LANGUAGE_CONSTRUCTS` mapping... for
language-aware syntax correction later."* This spec is that follow-up.

## Problem

Round 3 currently only lets a candidate direct an LLM through natural-language
instructions — there is no way to type or paste code directly. That's a real
restriction, not just an implementation gap: a candidate who already knows
exactly what to write has no faster path than dictating it sentence by
sentence. The round's actual goal is assessing whether the candidate knows
the programming logic and constructs a problem needs — and demonstrating
that by writing correct code directly is at least as strong a signal as
articulating it through the clarify-question flow. Restricting to
instructions-only doesn't serve that goal; it just adds friction for
candidates who could show the same knowledge faster.

## Goal

Let a candidate switch into an explicit "edit code directly" mode, type or
paste code into the actual code pane, and have it saved as the new current
code — with the LLM correcting only genuine syntax errors (never logic,
never style, no suggestions, no unsolicited fixes). The construct checklist
from the other spec still applies: this is a second way to *supply* an
answer to "does the solution use the required constructs," not a way to
skip being assessed on it. A candidate who pastes a complete, correct
solution has, by definition, already declared every construct it uses —
the checklist reads that from the code instead of asking about it.

## Scope

**In this pass:**
- A new "Edit code" UI toggle making the code pane directly editable (§1).
- A new turn kind, `direct_edit`, alongside `clarify`/`refuse`/`code_edit` (§2).
- A new syntax-only-fix prompt and orchestration function, separate from
  `round3_coding_turn.txt` (§3).
- Construct-checklist classification by reading the resulting code, feeding
  the same deterministic engine the natural-language path already uses (§4).
- Edge cases: unfixable syntax, turn-1 availability, re-declaring a
  construct differently than an earlier instruction did (§5).

**Explicitly out of scope:**
- Any change to the natural-language instruction flow itself
  (`round3_coding_turn.txt`, its `clarify`/`refuse`/`code_edit` rules) —
  this is a parallel, additive path, not a replacement.
- Any change to how `Run` or `Submit` work — both are unchanged; `Run`
  still only ever executes whatever `code_after` currently is, and `Submit`
  is still ungated by checklist completion (matching today's behavior,
  where a candidate can already submit with an incomplete instruction-based
  solution).
- Live, character-by-character syntax checking while typing. The fix pass
  runs once, on Save.

## §1 — UX flow

An "Edit code" toggle sits next to the code pane (`app.js`'s
`renderRound3CodingLayout`, the `round3-coding-pane` block currently
rendering `<pre class="code-snippet" id="round3-coding-code">` read-only).
Clicking it:

1. Replaces the `<pre>` with a `<textarea>` seeded with the current
   `code_after` (or empty if none yet — the toggle is available even
   before any code exists, see §5).
2. Replaces the pane's `Run`/`Submit` buttons with a single `Save` button
   for the duration of editing (Run against mid-edit, unsaved code would
   be ambiguous about what's actually being tested).
3. On Save: `POST /candidate/round/3/edit { code }` with the textarea's
   contents, then re-fetch state and re-render — same
   send-then-refetch pattern `round3CodingSendMessage` already uses for
   instruction turns. The pane switches back to read-only, showing
   whatever the LLM's syntax-fix pass returned as the new `code_after`.
   `Run`/`Submit` reappear, `Run` enabled if code exists (unchanged
   condition).

The instruction box and its own Send flow are untouched and usable at any
time — a candidate can freely alternate between directing the LLM by
instruction and editing code directly, turn by turn.

## §2 — New turn kind: `direct_edit`

`Round3CodingTurnResponse.response_kind` (`schemas.py`) gains a fourth
literal value: `Literal["clarify", "refuse", "code_edit", "direct_edit"]`.
The existing `_code_after_required_for_code_edit` validator's condition
broadens to require `code_after` for `code_edit` **or** `direct_edit` (a
direct edit always has resulting code — even an edit the LLM couldn't
usefully fix still saves the candidate's own code verbatim, see §5 — so
`code_after` is never optional on this path, unlike `clarify`).

`Round3TurnCreate` (instruction-shaped: `candidate_prompt`) is not reused
for this — a new schema:

```python
class Round3DirectEditCreate(BaseModel):
    code: str = Field(min_length=1)
```

A new router endpoint, `POST /round/3/edit`, creates a `Round3Turn` row the
same way `POST /round/3/turn` does, with `candidate_prompt` holding the
candidate's raw typed code (the column already means "what the candidate
submitted this turn" — code is what they submitted) and
`response_kind = "direct_edit"`. No new column, no new table — this reuses
`Round3Turn` exactly, including `declared_constructs_json` (§4).

## §3 — The syntax-fix prompt and orchestration

A new prompt file, `round3_syntax_fix.txt`, distinct from
`round3_coding_turn.txt` — the job here is fundamentally different
(correct syntax in *given* code vs. translate an *instruction* into new
code), so it gets its own focused prompt rather than a branch bolted onto
the existing one. Its rules, in outline:

1. Fix ONLY what would stop this code from parsing/compiling/running due
   to a syntax error — a missing colon, an unclosed bracket, a missing
   `;`, mismatched indentation, wrong keyword, that class of thing. If the
   code already parses cleanly, return it completely unchanged, with a
   short confirmatory `response_message` (e.g. "No syntax issues found.").
2. NEVER touch logic: never change what a comparison checks, never add or
   remove a branch, never fix an off-by-one, never rename anything, never
   add error handling, never "improve" style. If you can identify a syntax
   problem, fix only the minimal syntax needed to make it parse — the
   candidate's logic, right or wrong, is preserved exactly.
3. If you cannot identify a concrete syntax fix without guessing at the
   candidate's intent (the code is too broken/incomplete to know what
   syntax was meant), make no changes and say so plainly in
   `response_message` — consistent with this round's existing refuse(b)
   philosophy that the LLM never diagnoses on the candidate's behalf. The
   candidate's own code is still saved as `code_after` either way (see
   §5) — nothing is ever silently discarded.
4. Also perform Construct classification (§4) against every category in
   `required_constructs`, reading the *resulting* code (post-fix) for
   evidence — same category_status shape and same "never name the
   construct" framing is irrelevant here since there's no candidate-facing
   question on this path at all (see §4), so this section only needs the
   classification instructions, not the neutral-question-drafting ones.

New orchestration function in `llm_service.py`, parallel to
`round3_coding_turn` but simpler — no clarify branch, no leak-check, no
retry (there is no candidate-facing question to leak on this path):

```python
def round3_syntax_fix(
    code: str,
    language: str,
    required_constructs: list[str] | None = None,
    declared_constructs: dict | None = None,
) -> dict:
    ...
    # single _call_claude, Round3CodingTurnResponse.model_validate,
    # response_kind is always "direct_edit" on this path
    # category_status classified against ALL required_constructs (§4),
    # not just the previously-open ones
    return {
        "response_message": parsed.response_message,
        "code_after": parsed.code_after,
        "declared_constructs": updated_state,
    }
```

## §4 — Checklist classification reads the code, and the code is authoritative

Reuses the same `category_status` shape and the same engine
(`round3_construct_engine`) the instruction-based path already has — no
parallel gating mechanism. One behavioral difference from the instruction
path, worth calling out explicitly:

**Classification runs against every category in `required_constructs`,
not just the ones still open**, and the result *replaces* rather than
merges with a category's existing declared value when the code gives fresh
evidence. Rationale: after a direct edit, the code is the single source of
truth for what the solution actually does. If `iteration` was declared
`"a for loop"` from an earlier instruction, but the candidate's pasted code
uses a `while` loop instead, `declared_constructs["iteration"]` must
reflect the `while` loop that will actually run — not a stale value from
before the edit. This is the same principle the instruction path already
has for explicit corrections (`round3_coding_turn.txt`'s "Already
declared" block: *"If the candidate's new instruction... DOES explicitly
override one of these, classify that category as declared with the new
value"*) — a direct edit is the strongest possible form of that override,
since the code itself is the new value.

To avoid duplicating the merge logic that already exists inside
`round3_construct_engine.decide()`, extract a small shared helper:

```python
def merge_declared(category_status: dict, cumulative_state: dict, required_constructs: list) -> dict:
    """The declared-category merge step decide() already does, factored
    out so round3_syntax_fix can reuse it without going through decide()'s
    clarify/proceed branching, which has no meaning on a path with no
    candidate-facing question."""
    updated_state = dict(cumulative_state)
    for category, entry in category_status.items():
        if category in required_constructs and entry["status"] == "declared":
            updated_state[category] = entry["value"]
    return updated_state
```

`decide()` calls this at its top instead of its current inline loop;
`round3_syntax_fix`'s orchestration calls it directly, with no `decide()`
call at all — there is no clarify/proceed decision to make on this path,
only a state update.

**No gap-flagging.** If the resulting code still doesn't evidence every
required category, nothing is surfaced to the candidate about it — no
message, no hint, silence. Saying "your code doesn't have iteration yet"
would leak the construct by name more directly than anything the
neutral-question mechanism was built to prevent. The gap simply persists
in `declared_constructs_json` for scoring to see later, exactly like an
unaddressed category from the instruction path does.

## §5 — Edge cases

- **Unfixable syntax** (§3, rule 3): the candidate's own code is still
  saved as `code_after`, unmodified — never discarded, matching "Run is a
  real terminal... reading and fixing what went wrong is on you" already
  established for this round. The candidate can Run it to see the real
  error, or edit and Save again.
- **Turn 1 availability**: the toggle is available immediately, before any
  instruction-based turn — a candidate can go straight to typing a full
  solution as their very first action. The existing refuse(c)
  "solve-it-for-me" rule doesn't apply here at all: that rule exists to
  stop the *LLM* from deciding what to build when the candidate asks it
  to; typing your own code is the candidate deciding, not asking the LLM
  to.
- **Empty code pane**: Save with empty content is rejected client-side
  (`code: str = Field(min_length=1)`), same shape as the instruction box's
  existing empty-message guard.
- **Interaction with `Run`/`Submit`**: neither changes. `Run` always
  executes the current `code_after`, full stop. `Submit` is still
  ungated by checklist completion, matching today's behavior on the
  instruction path (a candidate can already submit an incomplete solution;
  a direct edit doesn't introduce a new way to bypass anything that
  wasn't already bypassable).

## Testing

Following the conventions the construct-checklist spec's own §6 already
established for this codebase:
- `llm_service` tests for `round3_syntax_fix`, monkeypatching `_call_claude`:
  clean code returned unchanged, a genuine syntax error fixed, an
  unfixable case leaves the candidate's code untouched with a plain
  explanation, `category_status` correctly replaces a stale declared value
  when the code contradicts it.
- `round3_construct_engine` tests for the extracted `merge_declared` helper
  in isolation, plus a regression test that `decide()`'s own behavior is
  unchanged after the refactor (same test suite that already covers it
  should still pass without modification).
- Router test for `POST /candidate/round/3/edit`: creates a `direct_edit`
  turn, persists `declared_constructs_json`, threads it into the next
  turn — same shape as the existing declared-constructs threading test for
  `POST /round/3/turn`.
- One real end-to-end test mirroring the construct-checklist spec's own
  full-flow test: paste a complete, correct solution as turn 1, assert
  `declared_constructs` reflects every required category read from the
  code, zero clarify turns needed.

## Open risks

- **Scoring weight is unspecified in this pass.** `round3_coding_scoring.txt`
  reads the full turn transcript holistically already, so a `direct_edit`
  turn will simply appear in it — but the prompt's wording currently
  frames precision entirely in terms of clarifying round-trips ("fewer
  'clarify' round-trips reflects better command of the problem"). Whether
  a candidate who never used the instruction path at all gets judged
  fairly by that wording, versus needing an explicit added sentence
  acknowledging direct edits as an equally valid signal, is left to be
  seen once real transcripts exist — not addressed mechanically in this
  pass.
- **A candidate could alternate edit/instruction turns to probe the
  checklist.** E.g., paste code missing one construct, see nothing
  flagged (by design, §4), then try an instruction to see if THAT
  triggers a clarify question, indirectly learning which category is
  still missing (though never its name or vocabulary — the existing
  leak-check still applies to any clarify question that results).
  Inherent to running two paths side by side; not a new class of leak
  beyond what the construct-checklist spec's own Open Risks already
  accepts about the classifier being probeable.
- **A construct declared via instruction is never retracted if a later
  direct edit drops it.** Classification only ever adds/overwrites
  category keys with fresh evidence from the current turn — it never
  deletes one. So a candidate could satisfy "iteration" through an
  instruction turn, then paste replacement code that doesn't actually
  use it, and the category stays marked satisfied from the earlier
  declaration. Consistent with the spec's own "replace when the code
  gives fresh evidence" wording (not a bug), but it's the most direct
  way the checklist could be gamed today, and worth flagging in case a
  future round wants the code to be re-verified against every declared
  category rather than only ever adding to them.
