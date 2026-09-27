# Practice-app engine - design contract

Round 2 candidates automate their own Round 1 test cases against a practice
app. Until now the AI wrote that app as new code for every scenario, in three
languages; about half the builds missed 1-2 test cases, each time for a new
reason. This replaces the AI-written code with **one engine, written and tested
once**, that runs a **scenario description** (JSON) the AI fills in and a
validator checks. No per-scenario code is written by the AI.

## Principles

1. **AI at the edges, proven code in the middle.** The AI writes the scenario
   description and the checklists (data). The runtime, the helper code
   candidates call, and all arithmetic, dates, money formatting and ordering
   are fixed code with tests.
2. **Two files per language.** The *candidate file* (main.py / main.js /
   Main.java, about 5-7 KB) is what the candidate edits and the Round 2
   assistant rewrites every turn: accounts, pages, record fields, every helper
   with what it does, one import line and the TODO block. The *engine file*
   (practice_engine.py / .js / PracticeEngine.java) holds the engine, the
   description and the `UI` / `API` / `Database` / `Test` helpers,
   `setup()` and `teardown(id=None)`; it is placed next to the candidate file
   whenever code runs (`execution_service.run_code(support_files=...)`, the
   checker's runners). Putting everything in one file made it ~45 KB, which
   every assistant turn would have had to rewrite. Candidates test the app
   through its helpers, as they would a real application; its rules and data
   are not in their file. Older practice apps (one self-contained file) run
   unchanged.
3. **Three languages, one behaviour.** The Python, JavaScript and Java runtimes
   implement one specification and pass one shared conformance suite (same
   description + same checklists -> identical values). Anything that could
   differ between languages (number formatting, rounding, date maths, regex,
   sort order, string case) is defined here and tested in all three.
4. **Every description is validated before use.** Schema, references
   (fields, entities, helpers), expression types, regex subset, seed data
   against field types. Problems go back to the AI once with the exact list;
   a description that still fails is reported to HR in plain words.
5. **No dead ends.** A test case the engine can't express is named "not
   supported" at Round 1 authoring time, before publishing, with the reason.

## The scenario description

```jsonc
{
  "app_name": "Library Management System",
  "base_url": "https://library.example.test",
  "now": "2024-02-10T10:00",              // fixed clock start; Test.advance_minutes moves it
  "entities": {                            // records the app stores
    "Book": {
      "key": "id",
      "id_format": "BK-{n:03}",            // optional: ids for records the app creates
      "fields": {"id": "string", "title": "string", "available_copies": "int",
                 "price": "money", "published": "date", "status": "string"}
    }
  },
  "data": {"Book": [{"id": "BK-001", "title": "The Great Gatsby", ...}]},
  "users": {                               // sign-in
    "entity": "Member", "login_field": "email", "password_field": "password",
    "name_field": "name", "session_minutes": 30,
    "blocked_when": {"eq": [{"field": "user.status"}, "Suspended"]},
    "messages": {"required_login": "...", "required_password": "...",
                 "invalid": "...", "blocked": "...", "expired": "...", "need_login": "..."}
  },
  "pages": ["Login", "Home", "Search", "Book Details", "My Books"],
  "home_page": "Home",
  "queries": [ ... ],                      // read-only lookups (search, lists, details)
  "actions": [ ... ],                      // anything that changes data or state
  "faults": [ ... ]                        // simulated failures a test can switch on
}
```

### Queries

```jsonc
{"name": "search_books", "label": "Search", "page": "Search", "entity": "Book",
 "match": {"input": "term", "fields": ["title", "author", "isbn"],
           "mode": "contains" | "equals" | "starts_with",          // always case-insensitive
           "blank": "all" | "empty" | "error:Please enter a search term",
           "max_length": 100, "too_long": "Search term is too long"},
 "inputs": [ ...extra inputs with checks, as for actions... ],
 "where": <condition>,                      // optional extra filter; `row.` is each record
 "order_by": [{"field": "title", "desc": false}],
 "show": ["id", "title", "author", "available_copies"],
 "none_message": "No books found",
 "next_page": "Results"}                    // optional
```
A query with a `key_input` instead of `match` returns one record (details
page): `{"name": "open_book", "entity": "Book", "key_input": "book_id",
"missing": "Book not found", "show": [...], "next_page": "Book Details"}`.
Queries need a signed-in user when the app has users, unless
`"requires_login": false`.

### Actions

```jsonc
{"name": "borrow_book", "label": "Borrow", "page": "Book Details",
 "requires_login": true,
 "inputs": [{"name": "book_id", "checks": [          // checked in order; first failure refuses
     {"rule": "required", "message": "Book is required"},
     {"rule": "pattern", "value": "BK-[0-9]{3}", "message": "Invalid book id"}]}],
     // rules: required, min_length, max_length, pattern, min, max, number, one_of,
     //        date, not_past, not_future (all but required skip an empty value)
 "load": [{"as": "book", "entity": "Book", "key": {"input": "book_id"}, "missing": "Book not found"}],
 "rules": [                                  // checked in order; the first that fails refuses the action
   {"unless": {"gt": [{"field": "book.available_copies"}, 0]}, "message": "No copies available"},
   {"unless": {"lt": [{"count": {"entity": "Loan", "where": {"eq": [{"field": "row.member"}, {"user": "id"}]}}}, 5]},
    "message": "You have reached your borrowing limit"}
 ],
 "effects": [
   {"set": {"record": "book", "field": "available_copies", "value": {"sub": [{"field": "book.available_copies"}, 1]}}},
   {"create": {"entity": "Loan", "as": "loan", "values": {"book": {"field": "book.id"}, "due": {"add_days": [{"today": true}, 14]}}}},
   {"send": {"channel": "email", "to": {"user": "email"}, "subject": "Book borrowed"}}
 ],
 "message": {"format": ["Book borrowed. Due date: {d}", {"d": {"format_date": [{"field": "loan.due"}, "DD-Mon-YYYY"]}}]},
 "next_page": "Borrow Confirmation",
 "returns": {"loan_id": {"field": "loan.id"}}}
```
Multi-step flows (OTP, confirm/cancel, payment) are sequences of actions that
keep state in `session` (`{"session": "otp"}` reads, `{"set_session": ...}`
writes) and use the clock (`{"minutes_since": {"session": "otp_sent_at"}}`).

### Expressions (JSON, identical meaning in every language)

Values: literals; `{"input": n}`; `{"field": "alias.field"}`; `{"user": field}`;
`{"session": key}`; `{"today": true}`, `{"now": true}`; `add` `sub` `mul`
`div` `round` (half-up, 2 decimals for money); `add_days`, `add_months`,
`days_between`, `minutes_since`; `count` / `sum` over an entity with a
condition (`row.` refers to each record); `format` (named placeholders);
`format_date` (`YYYY-MM-DD`, `DD-MM-YYYY`, `DD/MM/YYYY`, `MM/DD/YYYY`,
`DD-Mon-YYYY`, `DD Mon YYYY`);
`format_money` (`INR` groups 1,21,200.00; `USD`/`EUR`/`GBP` group 121,200.00;
symbol prefix); `upper`, `lower`, `trim`, `length`, `concat`.
Conditions: `eq` `ne` `lt` `le` `gt` `ge`, `and` `or` `not`, `empty`,
`matches` (regex subset: literals, `[]` classes, `\d \w \s`, `? * + {m,n}`,
`^ $`, groups, `|`; always full-string; `\d \w \s` are ASCII-only), `in`,
`exists` (entity + where).

Shared rules every runtime follows (the conformance test checks each):
numbers come back whole as integers, otherwise rounded half-up to 2 places
(2.675 -> 2.68, 0.1 x 3 -> 0.3); a number shown as text always has 2 decimals
("2.50"); lengths count characters, not bytes (an emoji is 1); upper/lower-case
and number formatting never depend on the server's language settings;
`add_months` keeps to the month's last day (31 Jan + 1 month = 29 Feb 2024).
Effects run all-or-nothing: if one fails, nothing the action did is kept.

### Faults

`{"name": "network_down", "applies_to": ["add_beneficiary"], "message": "Network error. Please try again."}`
- a test turns it on with `Test.simulate("network_down")`; the action refuses
with that message and changes nothing.

## Generated helpers (what candidates call)

Names come from the description, deterministically:

| Helper | Behaviour |
|---|---|
| `UI.open(page)` | go to a page (login required unless it's the login page) |
| `UI.login(login, password)` / `UI.logout()` | sign-in form |
| `UI.<query>(...)` / `UI.<action>(...)` | acts on the current page; returns results / `True`/`False`; sets the visible message; moves page |
| `UI.current_page()`, `UI.visible_message()`, `UI.signed_in_user()` | what the user sees |
| `API.<query>(...)` / `API.<action>(...)` | same logic without pages; returns `{"ok", "error", ...returns}` |
| `API.login(login, password)` | `{"ok", "error", "user"}` |
| `Database.get_<entity>(key)`, `Database.find_<entity>(field, value)`, `Database.count_<entity>(field=None, value=None)`, `Database.all_<entity>()` | what is really stored |
| `Database.outbox()` | emails/SMS/alerts sent |
| `Test.advance_minutes(n)`, `Test.simulate(fault)` | test support |
| `setup()` / `teardown(id=None)` | reset to the starting state / remove one created record |

Snake_case in Python; camelCase function names in JavaScript and Java
(returned field names stay as in the description); string values never change.

## Verification per scenario

1. AI writes the description -> validator -> (one correction) -> engine loads it.
2. AI writes one checklist per Round 1 reference test case against the
   generated helpers -> checklist validator (existing rules) -> run in Python.
3. A failing checklist is either the description or the checklist: the
   failure goes back to the AI to correct *data*, never code (up to 3 cheap
   rounds). The three languages cannot disagree, so JS/Java run once to confirm.
4. Result: every test case works, or is named "not supported" with the reason.

## Tests

- Engine unit tests per expression, effect, query and rule (Python).
- Conformance suite: descriptions + checklists run in Python, JavaScript and
  Java must give identical values.
- The 7 existing scenarios translated into descriptions by hand (reference
  set), then by the AI (2 runs each).
- Blind test: `tests/blind/SEALED_blind_scenarios.json`, opened only at the end.
