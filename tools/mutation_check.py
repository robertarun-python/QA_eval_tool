"""
Mutation check: deliberately breaks the Python engine in small, realistic ways
and runs the engine tests against each broken copy. Every break the tests do
NOT notice is a gap in the tests. Restores the file afterwards, always.

    .venv/bin/python tools/mutation_check.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "backend/app/services/practice_engine/runtime.py"
TESTS = ["tests/test_practice_engine.py", "tests/test_practice_engine_random.py", "tests/test_practice_engine_hostile.py"]

# (what the break means, text to find, what to put instead)
MUTATIONS = [
    ("failed change not undone", "            self.store, self.session, self.outbox, self.counters, self.created = saved\n", "            pass\n"),
    ("signed-in user not re-pointed after undo (the undo bug)",
     '            self.user = None if who is None else next((u for u in self.store[self.users["entity"]] if u.get(field) == who), None)\n',
     "            pass\n"),
    ("rule's 'then' effects dropped", '                if rule.get("then"):\n                    self._transaction(rule["then"], ctx)\n', ""),
    ("lockout never triggers", 'if self.failed_logins[who] >= lockout["attempts"]:', "if False:"),
    ("for_each rules skipped", '                for item in self._items(each, ctx):', "                for item in []:"),
    ("intermediate maths rounded to 2 places again", 'quantize(Decimal("1e-10")', 'quantize(Decimal("0.01")'),
    ("stray spaces in keys not ignored", "wanted = str(key).strip().lower()", "wanted = str(key).lower()"),
    ("refused action still sends email", "    def _check_rules(self, rules, ctx):", "    def _check_rules(self, rules, ctx):\n        self.outbox.append({'x': 'y'})"),
    ("unexpected problem crashes instead of refusing", "        except Exception:\n            raise _Refused(UNEXPECTED, status=500)\n\n    def run_query",
     "        except ZeroDivisionError:\n            raise _Refused(UNEXPECTED, status=500)\n\n    def run_query"),
    ("session never expires", "if minutes and self.last_active is not None", "if False and self.last_active is not None"),
]


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    missed = []
    try:
        for label, find, replace in MUTATIONS:
            if original.count(find) != 1:
                print(f"?? {label}: pattern not found once - update the mutation list")
                missed.append(label)
                continue
            TARGET.write_text(original.replace(find, replace), encoding="utf-8")
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings", *TESTS],
                                  cwd=ROOT, capture_output=True, text=True)
            caught = proc.returncode != 0
            print(("caught " if caught else "MISSED ") + label, flush=True)
            if not caught:
                missed.append(label)
    finally:
        TARGET.write_text(original, encoding="utf-8")
    print(f"\n{len(MUTATIONS) - len(missed)} of {len(MUTATIONS)} deliberate breaks caught")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
