@echo off
REM One-click setup + run for the QA Eval Tool.
REM Double-click this file, or run it from a terminal: run_server.bat

cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo Installing/checking dependencies (skips ones already installed)...
pip install -q -r requirements.txt

if not exist .env (
    echo.
    echo No .env file found - copying .env.example to .env.
    copy .env.example .env >nul
    echo.
    echo ============================================================
    echo  IMPORTANT: open .env in a text editor and paste in your
    echo  real ANTHROPIC_API_KEY ^(from console.anthropic.com^) and
    echo  review the seeded HR/candidate emails and passwords before
    echo  continuing. HR creating a scenario calls Claude right away
    echo  to generate the reference answer, so login alone works
    echo  without a real key but scenario creation will fail without one.
    echo ============================================================
    echo.
    pause
)

cd backend
echo.
echo Creating/checking the seeded HR + candidate accounts...
python -m app.seed
echo.
echo Starting server at http://127.0.0.1:8000/
echo Open that address in your browser once you see "Application startup complete" below.
echo Press Ctrl+C here to stop the server when you're done.
echo.
uvicorn app.main:app --reload
