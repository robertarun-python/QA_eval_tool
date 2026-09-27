"""
The slow pattern-B sweep, run before every paid round (about 30 s on 4 cores;
too slow for every test run): for each kind of key that can be left out of a
real description and still be accepted, one example is run in Python,
JavaScript and Java with odd inputs - a left-out key must be either refused
by the checker or given the same default by all three languages. No AI calls.

    .venv/bin/python tools/pattern_sweep.py
"""
import copy
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests")]

from app.services.practice_engine import validate  # noqa: E402
from test_real_output_patterns import BUILDS, _disagreements, _odd_steps, _paths, _spec  # noqa: E402

_NAMED = ("entities", "data", "fields", "returns", "values", "messages")


def _kind(at) -> str:
    """The path with list positions and the app's own names generalised."""
    parts = ["[]" if isinstance(p, int) else "<name>" if i and at[i - 1] in _NAMED else p for i, p in enumerate(at)]
    return "/".join(parts[:2] + ["..."] + parts[-2:]) if len(parts) > 6 else "/".join(parts)


def main() -> int:
    kinds = {}
    for path in BUILDS:
        spec = _spec(path)
        for at in _paths(spec):
            if not at or isinstance(at[-1], int) or at[0] == "data" or _kind(at) in kinds:
                continue
            s = copy.deepcopy(spec)
            parent = s
            for p in at[:-1]:
                parent = parent[p]
            if not isinstance(parent, dict):
                continue
            del parent[at[-1]]
            if not validate.problems(s):
                kinds[_kind(at)] = (path.stem, s)

    def run(item):
        kind, (name, s) = item
        steps, unexpected = _odd_steps(s)
        return kind, name, _disagreements(s, steps) + [f"something went wrong: {u}" for u in unexpected[:3]]

    with ThreadPoolExecutor(4) as pool:
        bad = [(k, n, p) for k, n, p in pool.map(run, kinds.items()) if p]
    for kind, name, problems in bad:
        print(f"{kind} ({name}):", *problems[:3], sep="\n    ")
    print(f"{len(kinds) - len(bad)} of {len(kinds)} kinds of left-out key behave the same in every language")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
