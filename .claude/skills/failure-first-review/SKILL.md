---
name: failure-first-review
description: Mandatory before calling any change to this project "done" - list the component's promises, test them with randomised sequences and fault injection, prove the tests with a mutation check, replay the library of real AI outputs, turn every failure into its pattern and a generator run over all real outputs, test guards both ways in every language, measure AI-dependent results as rates, and report what is NOT covered. Use for every engine, backend, prompt or UI change.
---

# Failure-first review

Why this exists (2026-09-26): an undo bug in the practice-app engine (a change
that failed half-way after signing the user out left the "signed-in user"
pointing at a record that no longer existed) passed every test. The tests were
written by the same mind as the code and checked each feature as intended, one
at a time - never combinations, never recovery. Hand-listed edge cases always
miss combinations; this procedure finds them mechanically.

Apply ALL steps before saying a change is done. Report each step's result as
numbers.

## 1. Promises (invariants) first
Write down what must ALWAYS be true for the component, whatever the input or
order, before writing tests. Examples: a refused action changes nothing; no
money is created or lost; the signed-in user is a real record; a helper never
raises; every language gives the same answer; a page never shows a raw error;
a paid AI call is never repeated by a retry the user didn't ask for.

## 2. Failure checklist - go through every line
- Input: missing, empty, spaces, wrong type, huge, unicode/emoji, injection text, negative, zero, boundary +/-1.
- Order: steps out of order, repeated, double-click, the same request twice.
- Time: expiry exactly at / just after the limit, clock jumps, day/month/leap-year ends.
- Partial failure: fail at EVERY point where data changes - is everything undone, including session/login state?
- Interruption: logout, session expiry, tab closed, page refreshed, server restarted, network drop mid-request.
- External services: the AI is slow, times out, errors, returns garbage or a cut-off reply.
- People: two users/tabs at once, HR and candidate acting on the same thing.
Each line is either tested, or written down as "not covered" with the reason.

## 3. Randomised sequence tests
Generate many random sequences (fixed seeds so failures reproduce) mixing
valid, invalid and hostile steps, and assert the promises from step 1 after
EVERY step. Example: tests/test_practice_engine_random.py.

## 4. Mutation check - prove the tests can fail
Deliberately break the code in small realistic ways (remove the undo, skip a
check, change a rounding, swallow a counter) and confirm the tests go red.
Every break that stays green is a gap: add the missing assertion, rerun.
Example: tools/mutation_check.py (must report N of N caught).

## 5. Independent adversarial review
A reviewer that did not write the code (a fresh agent given only the code and
the promises, no reasoning) tries to break it. Agents cost money - state the
cost and get the owner's yes first.

## 6. Real outputs, not only my own (added 2026-09-27)
Why: every offline test used inputs written by the same mind as the code - my
own descriptions, a fake AI that always answered neatly. The paid runs then
found ~15 causes at once: crashes on odd AI shapes, format gaps I had quietly
worked around, guards blocking correct work, run-to-run variation.
- **Real-output library.** Every real AI output (build descriptions,
  checklists, raw replies, assistant replies) is saved to
  tests/fixtures/practice_engine/real_outputs and replayed offline by
  tests/test_real_output_replay.py on every change. A failure seen once must
  never come back unnoticed; an accepted real output must stay accepted.
- **Early small pilot.** Before building on a new assumption about the AI's
  behaviour or a new format feature, get 2-3 real samples (about $1, with the
  owner's yes) - never discover the AI's habits at the end.
- **Two-sided tests for every guard.** Each check/validator is tested to catch
  bad input AND to let known-good real outputs through (false positives cost
  as much as misses: a correct reply blocked is a broken product).
- **Definition of done for every new feature**, including ones added in the
  middle of a measurement: 3-language conformance, randomised sequences, fuzz,
  a mutation in tools/mutation_check.py, and test data with empty/missing
  values for every field type.
- **Rates, not single tries.** AI-dependent results are measured as a pass
  rate over repeated runs (at least 3 per scenario for a final figure), and
  the variation is reported.
- **Honest labels.** Reports say "proven offline" and "proven with the real
  AI" separately; offline green is never reported as done for AI behaviour.

## 7. From one failure to its pattern (added 2026-09-27)
Why: scenarios and test cases differ every time, so a fix for the one
scenario where a failure showed up leaves its siblings open. The ~20 paid-run
failures fell into six patterns; each has a rule and a generator
(tests/test_real_output_patterns.py) run over EVERY real output. On first run
the generators found 8 more problems no scenario had hit yet.
For every new failure: name its pattern, fix the whole pattern, add or extend
its generator, and prove the generator fails on the old code.
- **A. The AI reaches for a word the format lacks** ("let", "contains").
  Rule: the format covers the everyday rule vocabulary (limits, caps,
  EMI, masking, age, remainders...), and every unknown word gets a message
  naming the right one (validate.OP_HINTS). Generator: list the words a normal
  programmer would use for the rules HR scenarios contain; each is either
  supported in all three languages or answered by a hint.
- **B. Right meaning, other shape** ("" for empty, a dict where a name goes,
  a JSON typo). Rule: every boundary accepts or explains, never crashes, and
  a slot's kind is checked (a list is a list) - "or []" hides wrong kinds that
  another language then trips on. Generator: corrupt real outputs (swap types,
  drop keys, wrap in lists, change case); accepted ones must run alike in all
  three languages. A dropped key is either required or has the same default
  everywhere (sweep every kind of deletion before a paid round).
- **C. Empty or odd values at run time.** Rule: every operation is defined on
  missing, empty, zero, negative zero, huge, tiny, text-for-number, lists,
  quotes and emoji, with one shared text form for numbers in every language.
  Generator: call every action and lookup of every real description with
  those values; no crash, no "something went wrong", all languages agree.
- **D. The AI reads an instruction too widely** ("must check the database"
  for a session-only action). Rule: every instruction states where it applies
  and gives the AI a way to say it disagrees (checklist_problem).
- **E. My own hand-offs between stages.** Rule: each stage is tested with the
  previous stage's real output, not a hand-made one (the replay library).
- **F. A guard misreads normal language.** Rule: every guard is tested both
  ways, in every language candidates use - Java first (85%), then JavaScript
  and Python - and in the styles people write (curly quotes, apostrophes,
  escaped JSON bodies, library names, printed text). Leak side: the forms a
  helpful model uses (a name joined to a part the candidate named, an unquoted
  value, a screen part, an offered choice).
- **Imagined vs recorded.** Variants I write are labelled "imagined"
  (tests/fixtures/practice_engine/imagined); they widen coverage but never
  replace real outputs - the pattern is only proven once real ones pass too.

## 8. Report
Plain words for the owner: promises checked, random steps run, breaks caught
(N of N), and the list of what is NOT covered. Never "should work".
