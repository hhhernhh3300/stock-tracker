"""Prompt-handling module: feeds aggregated market data to Claude and gets back a
structured, educational buy / sell / hold assessment.

Uses the official Anthropic SDK with:
  - claude-opus-4-8 (the default model)
  - adaptive thinking (the model decides how much to reason)
  - structured outputs (response validated against the StockAssessment schema)
  - prompt caching on the static system prompt (cheaper repeat calls)
"""
from __future__ import annotations

from typing import List, Literal

import anthropic
from pydantic import BaseModel, Field

MODEL = "claude-opus-4-8"

# The system prompt is STATIC across requests, so we cache it (cache_control below).
# Per-ticker data is volatile and goes in the user message (uncached).
SYSTEM_PROMPT = """You are a quantitative equity-research assistant. Given a snapshot of \
technical indicators, fundamentals, and Wall-Street analyst consensus for a single stock, \
produce a concise, structured read on whether the stock looks like a BUY, SELL, or HOLD \
candidate over the upcoming trading days (a roughly 1-4 week horizon).

How to weigh the inputs:
- Technicals. Trend (50-day vs 200-day SMA: golden cross is constructive, death cross is \
cautionary), price relative to those averages, RSI (>70 overbought / extended, <30 oversold), \
and MACD (line above signal = positive momentum). No single indicator is decisive — look for \
confluence and call out conflicts.
- Fundamentals. Valuation (P/E, forward P/E, PEG), growth (revenue/earnings), margins, and beta \
(volatility/risk). Strong momentum on weak fundamentals is higher risk.
- Analyst consensus. Treat the aggregate recommendation, number of analysts, and mean price \
target (and implied upside vs current price) as one input among several — not as truth.

Rules:
- Base every statement on the data provided. Do not invent news, earnings dates, or numbers. \
If a field is missing ("n/a"), say so rather than guessing.
- Be balanced: always surface both bullish and bearish factors.
- Set risk_level honestly using valuation stretch, RSI extremes, beta, and trend conflicts.
- Keep prose tight and plain-English; the reader may not be a professional investor.

COMPLIANCE (non-negotiable): You are NOT a licensed financial advisor and this is NOT \
financial advice or a personalized recommendation. Your output is for EDUCATIONAL and \
RESEARCH purposes only. Always populate the `disclaimer` field with this statement, and never \
tell the user to buy or sell — frame everything as an informational assessment of what the \
data suggests."""


class StockAssessment(BaseModel):
    """Schema the model must fill — enforced by structured outputs."""

    signal: Literal["buy", "sell", "hold"] = Field(
        description="Overall informational read for the next ~1-4 weeks."
    )
    conviction: Literal["low", "medium", "high"] = Field(
        description="How strongly the data points in the signal's direction."
    )
    risk_level: Literal["low", "moderate", "high", "very high"] = Field(
        description="Downside/volatility risk of acting on this read now."
    )
    time_horizon: str = Field(description="The horizon this read applies to, in plain words.")
    summary: str = Field(description="2-3 sentence plain-English bottom line.")
    bullish_factors: List[str] = Field(description="Concrete supportive points from the data.")
    bearish_factors: List[str] = Field(description="Concrete cautionary points from the data.")
    technical_read: str = Field(description="One paragraph on trend, RSI, and MACD together.")
    reasoning: str = Field(description="How the factors net out to the signal and risk level.")
    disclaimer: str = Field(description="Educational-use / not-financial-advice statement.")


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Reads ANTHROPIC_API_KEY from the environment.
        _client = anthropic.Anthropic()
    return _client


def _fmt(value, suffix="", pct=False):
    if value is None:
        return "n/a"
    if pct:
        return f"{value * 100:.1f}%" if abs(value) < 1.5 else f"{value:.1f}%"
    return f"{value}{suffix}"


def _build_user_message(snap: dict) -> str:
    m, q, i = snap["meta"], snap["quote"], snap["indicators"]
    f, a = snap["fundamentals"], snap["analyst"]
    cur = m.get("currency", "USD")
    return f"""Analyze this stock for the upcoming trading days.

COMPANY
  Ticker: {m['ticker']}   Name: {m.get('name')}
  Sector: {m.get('sector') or 'n/a'} / {m.get('industry') or 'n/a'}   Currency: {cur}

PRICE
  Last price: {_fmt(q.get('price'))}   Day change: {_fmt(q.get('day_change_pct'))}%
  52-week range: {_fmt(q.get('fifty_two_week_low'))} - {_fmt(q.get('fifty_two_week_high'))}

TECHNICALS
  50-day SMA: {_fmt(i.get('sma50'))}   200-day SMA: {_fmt(i.get('sma200'))}
  Price vs 50d: {_fmt(i.get('price_vs_sma50_pct'))}%   Price vs 200d: {_fmt(i.get('price_vs_sma200_pct'))}%
  Trend: {i.get('trend') or 'n/a'}
  RSI(14): {_fmt(i.get('rsi'))} ({i.get('rsi_zone') or 'n/a'})
  MACD: {_fmt(i.get('macd'))}  signal: {_fmt(i.get('macd_signal'))}  -> {i.get('macd_state') or 'n/a'}

FUNDAMENTALS
  Market cap: {_fmt(f.get('market_cap'))}   Beta: {_fmt(f.get('beta'))}
  Trailing P/E: {_fmt(f.get('trailing_pe'))}   Forward P/E: {_fmt(f.get('forward_pe'))}   PEG: {_fmt(f.get('peg_ratio'))}
  Revenue growth: {_fmt(f.get('revenue_growth'), pct=True)}   Earnings growth: {_fmt(f.get('earnings_growth'), pct=True)}
  Profit margin: {_fmt(f.get('profit_margin'), pct=True)}   Dividend yield: {_fmt(f.get('dividend_yield'), pct=True)}

ANALYST CONSENSUS (Yahoo aggregate)
  Recommendation: {a.get('recommendation') or 'n/a'} (mean {_fmt(a.get('recommendation_mean'))}, n={a.get('num_analysts') or 'n/a'})
  Mean target: {_fmt(a.get('target_mean'))}  (range {_fmt(a.get('target_low'))}-{_fmt(a.get('target_high'))})
  Implied upside vs price: {_fmt(a.get('target_upside_pct'))}%

Return your structured educational assessment."""


def analyze(snap: dict) -> dict:
    """Call Claude and return the validated assessment as a plain dict."""
    response = _get_client().messages.parse(
        model=MODEL,
        max_tokens=6000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": _build_user_message(snap)}],
        output_format=StockAssessment,
    )
    assessment = response.parsed_output
    if assessment is None:
        raise RuntimeError("Model did not return a parseable assessment.")
    return assessment.model_dump()
