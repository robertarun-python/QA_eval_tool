"""
App entrypoint. Run with: uvicorn app.main:app --reload (from backend/).
Interactive API docs land at http://127.0.0.1:8000/docs once running.
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import Base, engine
from .routers import auth, hr, candidate

# Creates tables on first run if they don't exist yet. Fine for a POC;
# a real project would use Alembic migrations instead once the schema
# needs to change without losing data.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="QA Eval Tool", version="0.1.0")

app.include_router(auth.router)
app.include_router(hr.router)
app.include_router(candidate.router)

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/")
def index(request: Request):
    """Minimal login + dashboard UI. All real logic lives behind the JSON API above."""
    # Each static asset's own mtime as a cache-busting query param - no
    # build step here, so without this a browser that already cached the
    # old file keeps running/rendering it after an edit until a hard
    # refresh forces a re-fetch.
    app_js_version = int((BASE_DIR / "static" / "app.js").stat().st_mtime)
    style_css_version = int((BASE_DIR / "static" / "style.css").stat().st_mtime)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "app_js_version": app_js_version,
        "style_css_version": style_css_version,
    })


@app.get("/health")
def health():
    return {"status": "ok"}
