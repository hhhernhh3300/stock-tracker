"""FastAPI entrypoint for the Analyst Agent.

Endpoints:
  GET /api/health              -> liveness check
  GET /api/analyze/{ticker}    -> market snapshot + Claude's educational assessment

Run:  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# read backend/.env regardless of the current working directory
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from . import analyst, market_data  # noqa: E402  (after load_dotenv)

GLOBAL_DISCLAIMER = (
    "For educational and research purposes only. This is not financial advice, "
    "not a personalized recommendation, and not the output of a licensed financial "
    "advisor. Markets are risky; do your own research and consult a professional "
    "before making any investment decision."
)

app = FastAPI(title="Analyst Agent API", version="1.0.0")

# CORS: open for local development. In production, restrict allow_origins to your
# deployed frontend's origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "ai_configured": bool(os.environ.get("ANTHROPIC_API_KEY"))}


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

    # 2) Claude assessment (degrade gracefully — still return data if the model call fails)
    assessment, ai_error = None, None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        ai_error = "ANTHROPIC_API_KEY is not set on the server."
    else:
        try:
            assessment = analyst.analyze(snapshot)
        except Exception as exc:
            ai_error = f"AI analysis failed: {exc}"

    return {
        **snapshot,
        "assessment": assessment,
        "ai_error": ai_error,
        "disclaimer": GLOBAL_DISCLAIMER,
    }
