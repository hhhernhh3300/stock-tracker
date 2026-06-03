"""FastAPI entrypoint for the Analyst Agent.

Endpoints:
  GET /api/health              -> liveness check + which engine is configured
  GET /api/analyze/{ticker}    -> market snapshot + an educational assessment

Assessment engine selection:
  - If an LLM provider key is configured (ANTHROPIC_API_KEY / OPENAI_API_KEY /
    GEMINI_API_KEY, choose with LLM_PROVIDER), the LLM produces the assessment.
  - Otherwise (or if the LLM call fails and ALLOW_RULES_FALLBACK is on), the free,
    deterministic rule-based engine in rules.py produces it instead.

Run:  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# read backend/.env regardless of the current working directory
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from . import analyst, market_data, rules  # noqa: E402  (after load_dotenv)

GLOBAL_DISCLAIMER = (
    "For educational and research purposes only. This is not financial advice, "
    "not a personalized recommendation, and not the output of a licensed financial "
    "advisor. Markets are risky; do your own research and consult a professional "
    "before making any investment decision."
)


def _truthy(val: str | None, default: bool = True) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# When True, fall back to the free rule-based engine if no LLM key is set or the
# LLM call fails. Defaults to True so the app is always useful out of the box.
ALLOW_RULES_FALLBACK = _truthy(os.environ.get("ALLOW_RULES_FALLBACK"), default=True)

app = FastAPI(title="Analyst Agent API", version="2.0.0")

# CORS: open for local development. In production, restrict allow_origins to your
# deployed frontend's origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    provider = analyst.resolve_provider()
    return {
        "status": "ok",
        "ai_configured": provider is not None,
        "llm_provider": provider,
        "llm_model": analyst.active_model(provider),
        "rules_fallback": ALLOW_RULES_FALLBACK,
    }


@app.get("/api/analyze/{ticker}")
def analyze(ticker: str) -> dict:
    ticker = (ticker or "").strip().upper()
    if not ticker or len(ticker) > 12 or not ticker.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Enter a valid ticker symbol, e.g. AAPL.")

    # 1) Market data + technical indicators
    try:
        snapshot = market_data.get_market_snapshot(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # network / provider hiccup
        raise HTTPException(status_code=502, detail=f"Could not fetch market data: {exc}")

    # 2) Assessment — LLM when configured, otherwise the free rule-based engine.
    assessment, ai_error = None, None
    if analyst.is_configured():
        try:
            assessment = analyst.analyze(snapshot)
        except Exception as exc:
            ai_error = f"AI analysis failed: {exc}"
            if ALLOW_RULES_FALLBACK:
                assessment = rules.assess(snapshot)
    elif ALLOW_RULES_FALLBACK:
        ai_error = "No LLM provider key set — used the free rule-based engine."
        assessment = rules.assess(snapshot)
    else:
        ai_error = "No LLM provider key set and rule-based fallback is disabled."

    return {
        **snapshot,
        "assessment": assessment,
        "engine": (assessment or {}).get("engine"),
        "ai_error": ai_error,
        "disclaimer": GLOBAL_DISCLAIMER,
    }


class ChatRequest(BaseModel):
    message: str
    ticker: str


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest) -> dict:
    ticker = (req.ticker or "").strip().upper()
    if not ticker or len(ticker) > 12 or not ticker.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Enter a valid ticker symbol.")

    try:
        snapshot = market_data.get_market_snapshot(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch market data: {exc}")

    if not analyst.is_configured():
        return {
            "response": (
                "No AI provider is configured on this server. "
                "Ask the site owner to set an API key (ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                "or OPENAI_COMPAT_API_KEY via Groq/OpenRouter)."
            ),
            "disclaimer": GLOBAL_DISCLAIMER,
        }

    try:
        response = analyst.chat(snapshot, req.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}")

    return {"response": response, "disclaimer": GLOBAL_DISCLAIMER}


# --------------------------------------------------------------------------- #
# Optional: serve the built React frontend from the SAME service (one URL, no
# CORS). Enabled automatically when a built frontend is found. Set the directory
# with FRONTEND_DIST, or it defaults to ../../frontend/dist relative to this file.
# API routes above take precedence; everything else falls back to index.html so
# the single-page app's client-side routing works.
# --------------------------------------------------------------------------- #
_FRONTEND_DIST = os.environ.get(
    "FRONTEND_DIST",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "frontend", "dist"),
)
_FRONTEND_DIST = os.path.abspath(_FRONTEND_DIST)
_INDEX_HTML = os.path.join(_FRONTEND_DIST, "index.html")

if os.path.isfile(_INDEX_HTML):
    # Serve hashed assets (JS/CSS) under /assets, and static files at the root.
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/")
    def _serve_index() -> FileResponse:
        return FileResponse(_INDEX_HTML)

    # SPA fallback: any non-API path that isn't a real file returns index.html.
    @app.exception_handler(StarletteHTTPException)
    async def _spa_fallback(request, exc):  # type: ignore[override]
        if exc.status_code == 404 and not request.url.path.startswith("/api"):
            return FileResponse(_INDEX_HTML)
        # default JSON error for API 404s and everything else
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
