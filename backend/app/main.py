"""
App entrypoint. Run with: uvicorn app.main:app --reload (from backend/).
Interactive API docs land at http://127.0.0.1:8000/docs once running.
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .database import Base, engine
from .services import fake_llm
from .routers import auth, hr, candidate, progressive

# Creates tables on first run if they don't exist yet. Fine for a POC;
# a real project would use Alembic migrations instead once the schema
# needs to change without losing data.
Base.metadata.create_all(bind=engine)

if settings.llm_fake_mode:
    print(f"*** {fake_llm.BANNER} (LLM_FAKE_MODE is on) ***", flush=True)

app = FastAPI(title="QA Eval Tool", version="0.1.0")

# Neither FastAPI nor Starlette impose a request-body size limit by
# default - without this, a single oversized request (JSON or the bulk-
# upload file) gets fully read into memory before any Pydantic field
# validation ever runs. 10MB is generous for every real payload here
# (the biggest is HR's candidate-roster .xlsx upload; every JSON body
# should be well under 1MB once schemas.py's own field length limits are
# in place) while still bounding the worst case.
#
# ponytail: Content-Length-based, not a running byte count against the
# actual stream - a request that omits Content-Length and streams via
# chunked transfer-encoding isn't caught here. Upgrade path if that
# matters for this deployment: wrap request.stream() and cut it off
# after MAX_REQUEST_BODY_BYTES actual bytes, not just checking the header.
MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            too_large = int(content_length) > MAX_REQUEST_BODY_BYTES
        except ValueError:
            too_large = False  # malformed header - let normal request handling reject it instead
        if too_large:
            return JSONResponse({"detail": "Request body too large."}, status_code=413)
    return await call_next(request)


app.include_router(auth.router)
app.include_router(hr.router)
app.include_router(candidate.router)
# Round 5 (Progressive Engineering) POC - a new, separate router, not
# merged into hr.router/candidate.router above and not added to
# candidate.py's ROUND_SEQUENCE gate. See routers/progressive.py.
app.include_router(progressive.candidate_router)
app.include_router(progressive.hr_router)

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
    response = templates.TemplateResponse("index.html", {
        "request": request,
        "app_js_version": app_js_version,
        "style_css_version": style_css_version,
        "fake_ai_banner": fake_llm.BANNER if settings.llm_fake_mode else "",
    })
    # The page itself must never be reused from the browser's cache - it
    # carries the ?v= versions above, so a stale copy keeps loading the old
    # app.js (seen: an old tab still showing a 7-minute limit after HR set 20).
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return {"status": "ok"}
