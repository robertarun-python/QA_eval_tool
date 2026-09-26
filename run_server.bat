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
echo Backing up the database (the last 10 startup backups are kept)...
python -m app.backup_db startup --keep 10
echo.
rem Default: no auto-restart (see run_server.sh). run_server.bat --dev only while developing.
set RELOAD=
if "%~1"=="--dev" (
    set RELOAD=--reload
    echo DEV MODE: the server restarts whenever code changes - not for real assessments.
)
echo Starting server at http://127.0.0.1:8000/
echo Open that address in your browser once you see "Application startup complete" below.
echo Press Ctrl+C here to stop the server when you're done.
echo.
uvicorn app.main:app %RELOAD%
