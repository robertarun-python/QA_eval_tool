#!/usr/bin/env bash
# One-click setup + run for the QA Eval Tool (macOS / Linux).
# Run it from a terminal: ./run_server.sh
# macOS/Linux counterpart of run_server.bat - same steps, same order.

set -e

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    # The code uses PEP 604 unions (e.g. `dict | None`), so it needs
    # Python 3.10+. macOS's built-in python3 is 3.9, so prefer a newer
    # interpreter if one is installed (e.g. `brew install python@3.12`).
    PYTHON=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
            PYTHON="$candidate"
            break
        fi
    done
    if [ -z "$PYTHON" ]; then
        echo "Python 3.10+ is required but wasn't found."
        echo "On macOS, install it with: brew install python@3.12"
        exit 1
    fi
    echo "Creating virtual environment with $PYTHON..."
    "$PYTHON" -m venv .venv
fi

source .venv/bin/activate

echo "Installing/checking dependencies (skips ones already installed)..."
pip install -q -r requirements.txt

if [ ! -f .env ]; then
    echo
    echo "No .env file found - copying .env.example to .env."
    cp .env.example .env
    # Fill in a random JWT secret so the app can start right away.
    SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
    sed -i.bak "s/^JWT_SECRET_KEY=.*/JWT_SECRET_KEY=$SECRET/" .env && rm -f .env.bak
    echo
    echo "============================================================"
    echo " IMPORTANT: open .env in a text editor and paste in your"
    echo " real ANTHROPIC_API_KEY (from console.anthropic.com) and"
    echo " review the seeded HR/candidate emails and passwords before"
    echo " continuing. HR creating a scenario calls Claude right away"
    echo " to generate the reference answer, so login alone works"
    echo " without a real key but scenario creation will fail without one."
    echo "============================================================"
    echo
    read -r -p "Press Enter to continue once .env is ready..." || true
fi

cd backend
echo
echo "Creating/checking the seeded HR + candidate accounts..."
python -m app.seed
echo
echo "Backing up the database (the last 10 startup backups are kept)..."
python -m app.backup_db startup --keep 10
echo
# Default: no auto-restart. With --reload, any code change restarts the
# server, and a restart waits for a running Round 2 practice-app build -
# freezing the whole site for candidates. Use ./run_server.sh --dev only
# while developing, never on an assessment day.
RELOAD=""
if [ "${1:-}" = "--dev" ]; then
    RELOAD="--reload"
    echo "DEV MODE: the server restarts whenever code changes - not for real assessments."
fi
echo "Starting server at http://127.0.0.1:8000/"
echo "Open that address in your browser once you see \"Application startup complete\" below."
echo "Press Ctrl+C here to stop the server when you're done."
echo
exec uvicorn app.main:app $RELOAD
