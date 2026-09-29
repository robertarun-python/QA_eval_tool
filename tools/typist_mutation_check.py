"""
Mutation check for the Round 2 assistant (round2_typist) and its Reference: breaks each rule on purpose
and runs its tests - every break must turn them red. Restores the files, always.

    .venv/bin/python tools/typist_mutation_check.py
"""
import subprocess, sys
from pathlib import Path
WT = Path(__file__).resolve().parent.parent
T = WT / "backend/app/services/round2_typist.py"
R = WT / "backend/app/services/practice_engine/reference.py"
S = WT / "backend/app/services/practice_engine/server.py"
TESTS = ["tests/test_round2_typist.py", "tests/test_real_output_replay.py", "tests/test_real_output_patterns.py", "tests/test_reference_screens.py",
         "tests/test_practice_run.py::test_the_api_paths_join_either_way", "tests/test_round2_reply_only.py"]
M = [
    (T, "a dropped step is let through", "dropped = [] if may_remove else _dropped(prior, steps)", "dropped = []"),
    (T, "a step left out of the code is let through", "left_out = _left_out_of_code(steps, code) if code else []", "left_out = []"),
    (T, "an invented step is let through", "invented = _not_theirs(prior, steps, reply_said)", "invented = []"),
    (T, "a changed typed value is let through", "retyped = _values_not_typed(said, code, own) if code else []", "retyped = []"),
    (T, "'Yes, continue. Next step...' counts as generate", "len((prompt or \"\")[yes.end():].split()) <= 4", "True"),
    (T, "'not enough details ... yet' counts as an offer", "and not _NOT_OFFER_RE.search(s)", ""),
    (T, "bare 'What's next?' stand-in", "            reply = _pick(_CODE_READY, used) if code is not None else _noted(candidate_prompt, used)",
     "            reply = \"What's next for this test?\""),
    (T, "offered steps/waits let through", "if _OFFER_RE.search(sentence) and not _OFFER_OK_RE.search(sentence):", "if False:"),
    (T, "API/database hint let through", "            if not (_API_DB_RE.search(said or \"\") or _DATA_WORD_RE.search(said or \"\")):", "            if False:"),
    (T, "variable placeholders read as values", "literals = ([_PLACEHOLDER_RE.sub(\" \", re.sub(", "literals = ([(lambda x: x)(re.sub("),
    (T, "small numbers read as values", "            if \".\" in n or len(n) >= 3:", "            if True:"),
    (T, "sentence-level repeat blocking back", "    return bool(reply) and _norm(reply) in {_norm(e) for e in earlier}",
     "    return bool(reply) and any(_norm(s) in _norm(' '.join(earlier)) for s in re.split(r'(?<=[.?!])\\s+', reply) if len(s.split()) >= 4)"),
    (T, "'write it now' only after a refusal", "        note = _WRITE_NOTE\n", "        pass\n"),
    (T, "the candidate's own request read as an offer", "r\"you want|you'?d like|you asked|you said|you (?:would|wish)|understood|i understand|as you)",
     "r\"zzz)"),
    (T, "'Perfect!' read as a value", '            token = token.rstrip("!*.?")  # "Perfect!" is a word, not a value', "            pass"),
    (T, "HTML tag names read as values", "td tr th tbody thead tfoot table div span li ul ol p a h1 h2 h3 h4 form label select option body html head img dl dt dd\n", ""),
    (T, "'proceed' not asking for code", "go ahead|proceed(?! to)|", "go ahead|"),
    (S, "a doubled api/ path is a 404", "        while path.startswith(\"/api/api/\"):\n            path = path[4:]\n", ""),
    (T, "a best-guess check withholds the code (the run-3 deadlock)",
     "        if code is None or unsaid(code, code_said, code=True):\n            code = best_code\n",
     "        if code is None or unsaid(code, code_said, code=True) or _left_out_of_code(steps, code) or _values_not_typed(said, code, own):\n            code = None\n"),
    (T, "an earlier value wins over the later one", "        if re.search(rf\"\\b{re.escape(fld)}\\b\", said[m.end():], re.I):\n            continue\n", ""),
    (T, "a reply-only redraft replaces the kept program", "            code, steps = keep, _steps_from(raw, steps)",
     "            code, steps = (raw or {}).get(\"code\") or keep, _steps_from(raw, steps)"),
    (T, "a reply-only redraft that fails doesn't fall back", "            keep, tried_reply_only = None, True", "            keep, tried_reply_only = None, False"),
    (R, "a value's visible label dropped from the Reference", "                    el[\"label\"] = self._term", "                    pass"),
]
caught = 0
for path, what, find, repl in M:
    orig = path.read_text()
    assert orig.count(find) == 1, f"not found: {what}"
    try:
        path.write_text(orig.replace(find, repl))
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings", *TESTS], cwd=WT, capture_output=True, text=True)
        ok = r.returncode != 0
    finally:
        path.write_text(orig)
    caught += ok
    print(("CAUGHT  " if ok else "MISSED  ") + what)
print(f"{caught} of {len(M)} breaks caught")
