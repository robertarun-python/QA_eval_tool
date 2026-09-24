#!/bin/sh
# Everything a change must pass before it is merged into main. Offline:
# no AI/API calls (the AI is faked; live replay tests stay skipped).
# Usage (from anywhere): scripts/check.sh [extra pytest args]
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
echo "== lint"
.venv/bin/ruff check backend tests
echo "== tests (includes type check, page/API contract and browser tests)"
$PY -m pytest tests -q -p no:cacheprovider "$@"
echo "== stored answers still fit their schemas"
(cd backend && ../$PY -m app.audit_content)
echo "All checks passed."
