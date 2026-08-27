# Round 3: Construct-Checklist Clarify Engine — Design

Addendum to `docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md`. That
spec defined Round 3's overall shape (candidate directs an LLM turn-by-turn;
`clarify` / `refuse` / `code_edit` classification; single evolving code
buffer). This spec replaces only the **Tier-1 clarify mechanism** described
there — today it is a single free-form LLM judgment call, and in practice it
clarifies reliably for missing *names* (a variable, a function) but not
reliably for missing *techniques* (which loop, which collection, which
comparison) and it can phrase a clarifying question in a way that names the
very construct it's testing for. This spec makes that mechanism mechanical
and checklist-driven instead of relying on prompt wording alone.

## Problem

The assessment's premise is that the candidate — not the assistant —
supplies every programming decision: what to store, how to represent it, how
to process it, how to decide, how to combine conditions. Today's prompt asks
a clarifying question only when the LLM judges, turn by turn, re-reading the
whole conversation, that something is missing. Two failure modes follow from
that being a per-turn judgment call rather than a mechanical check:

1. **Inconsistent triggering.** The same rule that reliably clarifies "what
   should I call this variable" does not reliably clarify "how should this
   be iterated" or "what should this be stored in" — nothing pins those
   categories down as *always* required, so the model sometimes infers a
   for-loop or a list on its own instead of asking.
2. **Vocabulary leakage.** Even when the model does ask, nothing stops the
   question from naming the construct category itself ("should this be a
   list or a dict?", "for loop or while loop?") — which answers the
   assessment question in the act of asking it.

## Goal

The candidate must explicitly supply every construct a correct solution
requires — collection type, iteration mechanism, comparison, conditional,
boolean combination, and so on — before the assistant writes code that uses
it. The assistant may ask what's missing, but must never name the category,
vocabulary, or possible choices while doing so. This is enforced
mechanically (a deterministic engine deciding what's missing and gating code
generation on it, backed by a mechanical vocabulary check), not left to
prompt wording alone — the same reasoning this codebase already applies to
the Tier-2 refusal guardrail (best-effort at generation, but the thing that
actually matters is checked, not just hoped for).

## Scope

**In this pass:**
- The construct taxonomy and per-language vocabulary (§1).
- HR-authored `required_constructs` per scenario (§2).
- Persisted, incrementally-updated declared-construct state per submission
  (§3).
- The turn-processing pipeline: classification, bundled clarify, mechanical
  leak-check, deterministic gating of `code_edit` (§4).
- The rewritten `round3_coding_turn.txt` prompt shape (§5).
- Testing (§6).
- One copy addition: the pre-round instructions state explicitly that the
  language choice is final once Round 3 starts (§7).

**Explicitly out of scope, deferred to a follow-up:**
- **Candidate-pasted code, syntax-only correction.** A mode where the
  candidate types actual source code instead of a natural-language
  instruction, and the assistant corrects only syntax errors — never logic,
  never style, no suggestions. This is a distinct interaction mode (the
  assistant classifying *typed code* rather than *an instruction about
  code*) that deserves its own design pass once this one has landed. Nothing
  in this design blocks it: the same `LANGUAGE_CONSTRUCTS` mapping (§1) this
  pass introduces is reusable for language-aware syntax correction later.
- **Changing the start-of-round language picker.** Round 3 already locks the
  language via a dropdown before turn 1 (`POST /candidate/round/3/start`,
  idempotent — a resubmitted `/start` with a different language is silently
  ignored and the original submission returned unchanged), and the frontend
  never re-renders that picker once a submission exists
  (`renderRoundView`'s `!submission` branch, `app.js:1919`). Both the
  mechanical lock and the UI lock already exist; this pass only adds the
  missing instructional copy telling the candidate up front that the choice
  is final (§7). No change to the start endpoint or the picker itself.

## §1 — Construct taxonomy

A new module, `backend/app/services/round3_constructs.py`, is the single
source of truth for every category, its generic (language-neutral) forbidden
vocabulary, and each supported language's actual keywords/idioms for it.

```python
# The full category set. Every scenario's required_constructs (§2) is a
# subset of these keys, in solve-order. Not every category needs a non-empty
# generic/language entry - some (e.g. primitive_type) lean entirely on the
# language layer.
CONSTRUCT_CATEGORIES = [
    "variable", "constant", "initialization", "assignment",
    "collection", "element_access", "key_value_access",
    "primitive_type", "reference_type", "type_conversion",
    "iteration", "iteration_source", "nested_iteration",
    "condition", "comparison", "boolean_logic",
    "function", "parameter", "return_value",
    "arithmetic_operation", "string_operation",
    "input", "output",
    "error_handling", "exception",
    "class", "object", "constructor", "method",
    "inheritance", "polymorphism", "encapsulation",
    "module", "import",
    "asynchronous_execution", "callback", "promise",
    "recursion", "generator", "anonymous_function", "higher_order_function",
]

# Language-neutral trigger words - forbidden in a clarifying question
# regardless of which language the submission is in, because they name the
# concept itself ("loop", "iterate") rather than a language's syntax for it.
GENERIC_VOCAB = {
    "variable": {"variable"},
    "collection": {"collection", "data structure"},
    "iteration": {"loop", "iterate", "iteration", "repeat", "cycle through"},
    "condition": {"condition", "conditional"},
    "comparison": {"compare", "comparison"},
    "boolean_logic": {"and", "or", "not", "boolean", "combine conditions"},
    "nested_iteration": {"nested", "inner loop", "outer loop"},
    "function": {"function", "method", "parameter", "argument"},
    "return_value": {"return"},
    "recursion": {"recursion", "recursive"},
    # ... remaining categories follow the same shape; several (e.g.
    # primitive_type, key_value_access) have no natural generic English
    # trigger and carry an empty set here, relying entirely on the
    # language layer below.
}

# Per-language literal keywords/idioms. Only the active submission's
# language layer is ever checked - a Java candidate is never penalized for
# Python's "def" leaking, because it's simply not in their forbidden set.
LANGUAGE_CONSTRUCTS = {
    "python": {
        "collection": {"list", "tuple", "set", "dict", "dictionary"},
        "iteration": {"for", "while"},
        "function": {"def", "lambda"},
        "class": {"class"},
        "asynchronous_execution": {"async", "await"},
        # ...
    },
    "java": {
        "collection": {"array", "arraylist", "hashmap", "hashset", "linkedlist"},
        "iteration": {"for", "while", "do-while", "enhanced-for"},
        "function": {"method"},
        "class": {"class"},
        # ...
    },
    "javascript": {
        "collection": {"array", "object", "map", "set"},
        "iteration": {"for", "while", "for-of", "for-in"},
        "function": {"function", "arrow function"},
        "asynchronous_execution": {"async", "await", "promise"},
        # ...
    },
}
```

The tables above show the shape and the categories Round 3's current problem
set actually exercises; implementation fills in the remaining categories
following the same pattern. This is a data-completeness task, not a design
gap — the mechanism that consumes these tables (§4) doesn't change shape
based on how many entries exist.

Each category also gets one hardcoded fallback question template, used only
if the LLM leaks vocabulary twice in a row for that category (§4) — e.g.
`iteration` → *"How should the program work through them, one at a time?"*

## §2 — HR authoring: `required_constructs`

`round3_reference_generation.txt` gains a third output alongside
`test_cases` and `expected_approach`:

```
3. The ordered list of construct categories (from the fixed taxonomy) a
   correct solution genuinely requires, in the order a candidate would
   naturally decide them - what to store, before how to process it, before
   what determines a match. Only include a category the problem actually
   needs.

{{"test_cases": [...], "expected_approach": "...",
  "required_constructs": ["collection", "iteration", "comparison", "condition"]}}
```

`generate_round3_reference` (`llm_service.py:173`) validates every entry
against `CONSTRUCT_CATEGORIES`, same style as its existing
`"test_cases" not in result"` check.

No new HR review UI. `reference_json` is already reviewed/edited as raw JSON
in a textarea (`app.js:782`); `required_constructs` is just one more key in
that same blob. One read-only summary line is added next to the existing
"Expected approach:" line (`app.js:778`) — *"Required concepts: collection,
iteration, comparison, condition"* — so HR sees the inferred list without
reading raw JSON.

## §3 — Data model: where declared state lives

One new column on `Round3Turn`:

```python
declared_constructs_json = Column(JSON, nullable=True)
```

The **cumulative** snapshot of what's been declared as of this turn — same
full-snapshot-not-diff convention already used for `code_after`. Written on
every turn:

- **clarify / code_edit**: classification (§4) runs against the previous
  turn's snapshot (or `{}` for turn 1); anything newly and committedly
  declared is merged in; the merged result is written here regardless of
  which `response_kind` this turn ends up being.
- **refuse**: no classification runs; the previous turn's snapshot is
  carried forward unchanged.

"What's currently declared" anywhere else (building the next prompt,
scoring) is always: the latest turn's `declared_constructs_json`, or `{}` if
no turns exist yet. One source of truth. Only categories in *this
scenario's* `required_constructs` are ever tracked — an off-topic mention
(e.g. recursion on a problem that doesn't need it) is not recorded.

## §4 — Turn-processing pipeline

Existing Tier-1c/Tier-2 checks (solve-it-for-me, judgment-call,
self-diagnosis-dodge) run first and are unchanged — they're orthogonal to
construct tracking. What follows applies once an instruction is judged
actionable.

**One LLM call, extended output.** Alongside `response_kind` /
`response_message` / `code_after`, the model returns `category_status` — one
entry per required category **not already in the cumulative declared
state**:

```json
"category_status": {
  "iteration":  {"status": "attempted_but_vague", "neutral_question": "How will your program work through them, one at a time?"},
  "comparison": {"status": "not_addressed",        "neutral_question": "What should determine whether one value replaces another?"},
  "collection": {"status": "declared", "value": "a list called salaries"}
}
```

Three statuses:
- **declared** — this message commits to a specific choice (a name, a
  technique, a structure).
- **attempted_but_vague** — this message is clearly about this category but
  doesn't commit ("loop through them" for iteration). A hedge or musing ("I
  think maybe a loop") is also `attempted_but_vague`, never `declared` —
  only a committed instruction counts.
- **not_addressed** — this message says nothing relevant to this category.

Every not-yet-declared category gets a drafted `neutral_question`
regardless of status, so the engine always has phrasing ready.

**The engine (`round3_construct_engine.py`, plain deterministic Python, no
LLM calls of its own) then decides — never the model's self-reported
`response_kind`:**

1. `refuse` from the model is trusted outright.
2. Otherwise, merge every `declared` category into the cumulative state.
3. If any required category is still not `declared`:
   - Categories this message left `attempted_but_vague` are asked about
     **together** — one bare neutral line per gap. This is the
     bundled-instruction case: a candidate who tries several steps in one
     message and leaves more than one underspecified gets asked about all
     of them at once, not forced to resend the same bundle repeatedly.
   - If nothing was attempted (a pure goal statement, or everything
     attempted was fully resolved) but categories remain `not_addressed`,
     the engine picks the single earliest one in the scenario's authored
     order and asks just that — the step-by-step interview case.
   - Final kind: `clarify`. `code_after` is discarded even if the model
     produced one.
4. Only when every required category is `declared` does the engine allow
   `code_edit`, using the model's `code_after`.

**Leak-check**, run on each selected `neutral_question` independently,
against `GENERIC_VOCAB[category] | LANGUAGE_CONSTRUCTS[language][category]`
for the submission's actual language. A leak triggers one regeneration of
the whole call; a second leak on the same category falls back to that
category's hardcoded template, keeping whatever else came back clean. The
`response_message` the candidate actually sees, for a clarify turn, is the
engine's concatenation of the selected post-leak-check questions — never the
model's own free-text `response_message`. For `refuse` and `code_edit`
turns, the model's own `response_message` is used as-is (a short note),
matching today.

An explicit candidate correction to an already-`declared` category ("use a
while loop instead") is always honored — the guardrail against re-asking or
hinting applies to the assistant, never to the candidate changing their own
mind.

## §5 — `round3_coding_turn.txt` shape

Same `refuse` / `clarify` / `code_edit` framing at the top; Tier-1c/Tier-2
text is unchanged. Additions:

- **New template inputs**: `required_constructs` (this scenario's ordered
  category list — internal keys, fine for the model to see, never surfaced
  to the candidate), `declared_constructs` (cumulative state so far), and
  each not-yet-declared category's forbidden-vocabulary set (so the model
  tries to avoid it before the mechanical gate has to intervene).
- **New instruction block** replacing today's single "ask ONE bare
  question" paragraph: classify each not-yet-declared required category
  against *this* instruction only (state from earlier turns is given as
  fact, never re-derived from the transcript); draft a neutral question per
  category, worded generically, obeying that category's forbidden
  vocabulary.
- **Established-value guardrail** now reads from `declared_constructs`
  directly instead of re-inferring from transcript history — consistent
  regardless of conversation length.
- **Code-edit discipline** (only change what's asked, full snapshot, no
  unrelated fixes) is unchanged.

`Round3CodingTurnResponse` (schemas.py) gains an optional `category_status`
field, validated for internal shape only — the schema doesn't know about
`required_constructs`; that cross-check is the engine's job. The
merge/gate/leak-check logic lives in the new
`backend/app/services/round3_construct_engine.py`, called from (or wrapping)
`llm_service.round3_coding_turn`, kept separate from the LLM-calling
function so the deterministic parts are plain, mock-free Python.

## §6 — Testing

**Pure-Python, no LLM mocking** (`tests/test_round3_construct_engine.py`,
new):
- Merging a `category_status` block declares the right categories and
  leaves the rest untouched.
- Multiple `attempted_but_vague` categories in one message produce a
  bundled clarify covering all of them, not just one.
- Nothing attempted, categories still `not_addressed` → probes exactly the
  earliest one in scenario order.
- Every required category `declared` → engine allows `code_edit` and passes
  through `code_after`; any one short → engine forces `clarify` and
  discards `code_after`, even if the model produced one.
- Leak-check: a `neutral_question` containing a forbidden word (generic or
  language-specific, for the submission's actual language) is rejected; a
  clean regeneration is accepted; a second leak on the same category falls
  back to the hardcoded template.
- An explicit correction to an already-`declared` category updates the
  stored value.

**`llm_service` tests** (extending `tests/test_llm_service_round3_coding.py`,
same monkeypatch-`_call_claude` style as today): the extended
`Round3CodingTurnResponse` accepts a valid `category_status` block and
rejects a malformed one.

**End-to-end** (extending `tests/test_round3.py`):
- A multi-turn happy path through the new flow: goal statement →
  single-category probe → bundled instruction with two gaps → bundled
  clarify with two questions → final instruction → `code_edit`, verifying
  `Round3Turn.declared_constructs_json` accumulates correctly and survives
  being read back mid-conversation.
- A vocabulary-audit test, the direct analog of the existing
  `test_scoring_prompt_injection_guardrail.py`: run one realistic clarify
  scenario per category and mechanically assert `response_message` never
  contains that category's forbidden words for the submission's language.
- A regression test that a category declared in turn 2 is still honored —
  never re-asked, never hinted at — by turn 8 of a long conversation.

**HR-authoring test** (extending the existing
`test_hr_can_edit_a_draft_round3_scenarios_reference_json`-style tests):
`generate_round3_reference` rejects a `required_constructs` entry that
isn't a known taxonomy key, same style as its existing shape checks.

## §7 — Language-lock instructional copy

Two copy additions, no logic change (the lock is already enforced both
server-side — `start_round3`'s idempotent return, `candidate.py:265-267` —
and client-side — the language `<select>` only ever renders in
`renderRoundView`'s `!submission` branch, `app.js:1919`):

- One bullet added to `showRound3CodingIntro()`'s modal list (`app.js:2354`):
  the language choice is final once Round 3 starts.
- A short note next to the language `<select>` itself in
  `confirmRound3CodingIntro()` (`app.js:2384`) reiterating it's a one-time
  choice, right where the candidate is about to make it.

## Open risks / explicitly deferred

- **Taxonomy completeness is a data task, not a design one** (§1) — the
  full `GENERIC_VOCAB`/`LANGUAGE_CONSTRUCTS` tables for every category in
  `CONSTRUCT_CATEGORIES` are filled in during implementation, following the
  pattern shown.
- **Extra LLM cost on leak retries.** The common case is unchanged from
  today (one call per turn); a vocabulary leak costs one extra call. Not
  benchmarked against the round's time limit in this pass, consistent with
  the existing design's own noted gap on multi-turn latency.
- **Candidate-pasted-code syntax correction** is a separate design pass
  (Scope, above).
