---
name: failure-first-review
description: Mandatory before calling any change to this project "done" - list the component's promises, test them with randomised sequences and fault injection, prove the tests with a mutation check, and report what is NOT covered. Use for every engine, backend, prompt or UI change.
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

## 6. Report
Plain words for the owner: promises checked, random steps run, breaks caught
(N of N), and the list of what is NOT covered. Never "should work".
