"""Provider-agnostic LLM analyst module.

Feeds an aggregated market-data snapshot to a large language model and gets back a
structured, educational buy / sell / hold assessment validated against the
`StockAssessment` schema.

Supported providers (selected via the LLM_PROVIDER env var, or auto-detected from
whichever API key is present):

    LLM_PROVIDER=anthropic   -> Anthropic Claude     (ANTHROPIC_API_KEY)
    LLM_PROVIDER=openai      -> OpenAI GPT           (OPENAI_API_KEY)
    LLM_PROVIDER=gemini      -> Google Gemini        (GEMINI_API_KEY / GOOGLE_API_KEY)
    LLM_PROVIDER=auto        -> first provider whose key is set (default)

Each provider's model can be overridden with an env var (see DEFAULT_MODELS). The
SDKs are imported lazily so you only need the package for the provider you use.

This module is OPTIONAL — if no provider key is configured the API falls back to the
free, deterministic rule-based engine in ``rules.py``.
"""
from __future__ import annotations

import json
import os
from typing import List, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Sensible, current default model per provider. Override with the matching env var.
DEFAULT_MODELS = {
    "anthropic": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
    "openai": os.environ.get("OPENAI_MODEL", "gpt-4o-2024-11-20"),
    "gemini": os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"),
    # OpenAI-COMPATIBLE gateways (Groq, OpenRouter, Mistral, Cerebras, LiteLLM,
    # corporate gateways). No universal default model — set OPENAI_COMPAT_MODEL.
    "openai_compatible": os.environ.get("OPENAI_COMPAT_MODEL", ""),
}

# Map provider -> env var(s) that hold its key (checked in order).
_PROVIDER_KEYS = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openai_compatible": ["OPENAI_COMPAT_API_KEY", "LLM_GATEWAY_API_KEY"],
}

# Base URL for the OpenAI-compatible gateway (required for that provider).
# Examples:
#   Groq        https://api.groq.com/openai/v1
#   OpenRouter  https://openrouter.ai/api/v1
#   Mistral     https://api.mistral.ai/v1
#   Cerebras    https://api.cerebras.ai/v1
def _compat_base_url() -> str | None:
    return (
        os.environ.get("OPENAI_COMPAT_BASE_URL")
        or os.environ.get("LLM_GATEWAY_BASE_URL")
        or None
    )


# Order tried when LLM_PROVIDER=auto.
_AUTO_ORDER = ["anthropic", "openai", "gemini", "openai_compatible"]


# --------------------------------------------------------------------------- #
# Prompt (shared across all providers)
# --------------------------------------------------------------------------- #

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
data suggests.

Respond with a single JSON object only — no markdown, no prose outside the JSON — matching \
exactly this shape:
{
  "signal": "buy" | "sell" | "hold",
  "conviction": "low" | "medium" | "high",
  "risk_level": "low" | "moderate" | "high" | "very high",
  "time_horizon": string,
  "summary": string,
  "bullish_factors": [string, ...],
  "bearish_factors": [string, ...],
  "technical_read": string,
  "reasoning": string,
  "disclaimer": string
}"""


class StockAssessment(BaseModel):
    """Schema the model must fill — validated after the call."""

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


# --------------------------------------------------------------------------- #
# Provider selection
# --------------------------------------------------------------------------- #


def _key_for(provider: str) -> str | None:
    for env in _PROVIDER_KEYS.get(provider, []):
        val = os.environ.get(env)
        if val:
            return val
    return None


def resolve_provider() -> str | None:
    """Return the provider to use, or None if no key is configured."""
    requested = (os.environ.get("LLM_PROVIDER") or "auto").strip().lower()
    if requested in _PROVIDER_KEYS:
        return requested if _key_for(requested) else None
    # auto (or unknown value) -> first provider with a key
    for provider in _AUTO_ORDER:
        if _key_for(provider):
            return provider
    return None


def is_configured() -> bool:
    return resolve_provider() is not None


def active_model(provider: str | None = None) -> str | None:
    provider = provider or resolve_provider()
    return DEFAULT_MODELS.get(provider) if provider else None


# --------------------------------------------------------------------------- #
# Prompt rendering
# --------------------------------------------------------------------------- #


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

Return your structured educational assessment as a single JSON object."""


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of model text, tolerating markdown fences."""
    text = (text or "").strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


# --------------------------------------------------------------------------- #
# Provider implementations
# --------------------------------------------------------------------------- #


def _call_anthropic(user_msg: str) -> dict:
    import anthropic  # lazy import

    client = anthropic.Anthropic(api_key=_key_for("anthropic"))
    resp = client.messages.create(
        model=DEFAULT_MODELS["anthropic"],
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
    return _extract_json(text)


def _call_openai(user_msg: str) -> dict:
    from openai import OpenAI  # lazy import

    client = OpenAI(api_key=_key_for("openai"))
    resp = client.chat.completions.create(
        model=DEFAULT_MODELS["openai"],
        max_tokens=2000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    return _extract_json(resp.choices[0].message.content)


def _call_gemini(user_msg: str) -> dict:
    import google.generativeai as genai  # lazy import

    genai.configure(api_key=_key_for("gemini"))
    model = genai.GenerativeModel(
        model_name=DEFAULT_MODELS["gemini"],
        system_instruction=SYSTEM_PROMPT,
        generation_config={"response_mime_type": "application/json"},
    )
    resp = model.generate_content(user_msg)
    return _extract_json(resp.text)


def _call_openai_compatible(user_msg: str) -> dict:
    """Any OpenAI-compatible /v1 gateway: Groq, OpenRouter, Mistral, Cerebras,
    LiteLLM, or a corporate model gateway. Reuses the openai SDK with a custom
    base_url. The key + URL + model all come from the environment."""
    from openai import OpenAI  # lazy import

    base_url = _compat_base_url()
    if not base_url:
        raise RuntimeError(
            "openai_compatible provider needs OPENAI_COMPAT_BASE_URL (or "
            "LLM_GATEWAY_BASE_URL) set, e.g. https://api.groq.com/openai/v1"
        )
    model = DEFAULT_MODELS["openai_compatible"]
    if not model:
        raise RuntimeError(
            "openai_compatible provider needs OPENAI_COMPAT_MODEL set, e.g. "
            "llama-3.3-70b-versatile (Groq) or a model id your gateway exposes."
        )
    client = OpenAI(api_key=_key_for("openai_compatible"), base_url=base_url)
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=2000,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
    except Exception:
        # Some gateways don't support response_format=json_object — retry without it
        # (the prompt already instructs JSON-only output, and _extract_json is lenient).
        resp = client.chat.completions.create(
            model=model,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
    return _extract_json(resp.choices[0].message.content)


_DISPATCH = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
    "openai_compatible": _call_openai_compatible,
}


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #


def analyze(snap: dict) -> dict:
    """Call the configured LLM provider and return the validated assessment as a dict.

    Raises RuntimeError if no provider is configured, or the underlying SDK/HTTP
    error if the call itself fails (callers should degrade gracefully).
    """
    provider = resolve_provider()
    if provider is None:
        raise RuntimeError(
            "No LLM provider configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or "
            "GEMINI_API_KEY (and optionally LLM_PROVIDER)."
        )

    user_msg = _build_user_message(snap)
    raw = _DISPATCH[provider](user_msg)

    assessment = StockAssessment(**raw)
    result = assessment.model_dump()
    result["engine"] = provider          # e.g. "anthropic"
    result["model"] = DEFAULT_MODELS[provider]
    return result
