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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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


@app.middleware("http")
async def _no_store_api(request, call_next):
    """Stop browsers/webviews caching live market data.

    /api/analyze/{ticker} is a GET with no Cache-Control header, so a browser
    (especially a mobile webview) is free to heuristically cache it and re-serve
    a stale snapshot — e.g. an empty payload captured during a Yahoo rate-limit
    window — even after the backend recovers. Forcing `no-store` on every API
    response guarantees each search hits the live endpoint and gets fresh data.
    Static assets (the hashed JS/CSS bundle) are unaffected and stay cacheable.
    """
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/api/health")
def health() -> dict:
    provider = analyst.resolve_provider()
    chain = analyst.resolve_provider_chain()
    return {
        "status": "ok",
        "ai_configured": provider is not None,
        "llm_provider": provider,                 # primary (first in the chain)
        "llm_provider_chain": chain,              # full failover order (e.g. [gemini, openai_compatible])
        "llm_model": analyst.active_model(provider),
        "rules_fallback": ALLOW_RULES_FALLBACK,
        "yahoo_proxy": market_data.proxy_configured(),  # True when YAHOO_PROXY_BASE is set
    }


@app.get("/api/diag/llm")
def diag_llm() -> dict:
    """Ping every configured LLM provider independently (1-token round-trip) so
    each one — including the Cerebras/2nd-gateway fallback — can be verified
    without changing the production failover order. Returns per-provider ok/error."""
    chain = analyst.resolve_provider_chain()
    return {
        "chain": chain,
        "providers": {p: analyst.ping(p) for p in chain},
    }


@app.get("/api/search")
def search(q: str = "") -> dict:
    """Live symbol search for the autocomplete box.

    Returns up to ~12 matching symbols (stocks, ETFs, indices, FX, crypto) from
    Yahoo's global search index for the partial name/ticker in `q`. Best-effort:
    returns an empty list rather than erroring on a bad/empty query."""
    query = (q or "").strip()
    if not query:
        return {"query": "", "results": []}
    try:
        results = market_data.search_symbols(query)
    except Exception:
        results = []
    return {"query": query, "results": results}


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


class ChatTurn(BaseModel):
    role: str = "user"
    text: str = ""


class ChatRequest(BaseModel):
    message: str
    ticker: str
    history: list[ChatTurn] | None = None


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    """Free-form Q&A about a ticker, grounded in a fresh market snapshot.

    Requires an LLM provider to be configured (the rule-based engine can't hold a
    conversation). Returns {"reply", "engine", "model", "ticker"}.
    """
    ticker = (req.ticker or "").strip().upper()
    question = (req.message or "").strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="A ticker is required for chat.")
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question to chat.")
    if not analyst.is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "AI chat is unavailable: no LLM provider key is configured on the "
                "server. Set an API key (e.g. Groq/OpenAI/Gemini) to enable chat."
            ),
        )

    try:
        snapshot = market_data.get_market_snapshot(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch market data: {exc}")

    history = [t.model_dump() for t in (req.history or [])]
    try:
        result = analyst.chat(snapshot, question, history)
    except Exception as exc:
        msg = str(exc)
        low = msg.lower()
        # Free-tier LLM quota / rate-limit → a short, friendly message instead of
        # dumping Google/Groq's raw 429 payload into the chat bubble.
        if "429" in msg or "quota" in low or "rate limit" in low or "rate_limit" in low:
            raise HTTPException(
                status_code=429,
                detail=(
                    "⏳ The AI is rate-limited right now — the free LLM tiers (Gemini / "
                    "Groq) have a daily request cap and it's been reached. Please try "
                    "again in a few minutes, or later today once the free quota resets. "
                    "All the market data, charts and risk/opportunity scoring still work."
                ),
            )
        raise HTTPException(status_code=502, detail=f"AI chat failed: {exc}")

    return {
        "reply": result["reply"],
        "engine": result["engine"],
        "model": result["model"],
        "ticker": ticker,
        "disclaimer": GLOBAL_DISCLAIMER,
    }


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
