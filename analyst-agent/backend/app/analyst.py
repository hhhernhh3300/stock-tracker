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
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Sensible, current default model per provider. Override with the matching env var.
DEFAULT_MODELS = {
    "anthropic": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
    "openai": os.environ.get("OPENAI_MODEL", "gpt-4o-2024-11-20"),
    # NOTE: this is just the *preferred* Gemini model. Google rotates/deprecates
    # model ids on the API, so the Gemini callers actually try a fallback chain
    # (_gemini_candidates) and use the first one that works. Set GEMINI_MODEL to
    # pin a specific id.
    "gemini": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    # OpenAI-COMPATIBLE gateways (Groq, OpenRouter, Mistral, Cerebras, LiteLLM,
    # corporate gateways). No universal default model — set OPENAI_COMPAT_MODEL.
    #   Groq examples:  llama-3.3-70b-versatile, llama-3.1-8b-instant,
    #                   llama-4-scout-17b-16e-instruct, llama-4-maverick-17b-128e-instruct,
    #                   mixtral-8x7b-32768
    #   Mistral examples: mistral-large-latest, mistral-small-latest, open-mixtral-8x22b
    "openai_compatible": os.environ.get("OPENAI_COMPAT_MODEL", ""),
    # A SECOND OpenAI-compatible gateway, so you can stack another free provider
    # (e.g. Cerebras / OpenRouter) after Groq for more daily quota headroom.
    #   Cerebras:   OPENAI_COMPAT2_BASE_URL=https://api.cerebras.ai/v1
    #               OPENAI_COMPAT2_MODEL=llama-3.3-70b   (or gpt-oss-120b, qwen-3-…)
    #   OpenRouter: OPENAI_COMPAT2_BASE_URL=https://openrouter.ai/api/v1
    #               OPENAI_COMPAT2_MODEL=meta-llama/llama-3.3-70b-instruct:free
    "openai_compatible2": os.environ.get("OPENAI_COMPAT2_MODEL", ""),
}

# Gemini fallback chain. Tried in order until one returns successfully. A pinned
# GEMINI_MODEL (if set) is always tried first. Override with GEMINI_MODELS
# (comma-separated).
#
# FREE-TIER NOTE: kept to FLASH-class models only, on purpose. Each model has its
# OWN free-tier daily quota, but the Pro models (gemini-*-pro) have tiny free
# quotas (and gemini-3.x-pro is free-tier limit 0), and the "*-latest" aliases
# resolve to whichever model Google rotates in — often a Pro id that 429s
# immediately. Trying those just burned quota and produced noisy errors. Flash
# models have generous free quotas and are plenty for this summarisation task.
_GEMINI_FALLBACKS = [  # used only when GEMINI_MODELS isn't set
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
]


def _gemini_candidates() -> list[str]:
    """Ordered, de-duplicated list of Gemini model ids to try."""
    override = (os.environ.get("GEMINI_MODELS") or "").strip()
    if override:
        ids = [m.strip() for m in override.split(",") if m.strip()]
    else:
        ids = [DEFAULT_MODELS["gemini"], *_GEMINI_FALLBACKS]
    seen: set[str] = set()
    out: list[str] = []
    for m in ids:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out

# Map provider -> env var(s) that hold its key (checked in order).
_PROVIDER_KEYS = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openai_compatible": ["OPENAI_COMPAT_API_KEY", "LLM_GATEWAY_API_KEY"],
    "openai_compatible2": ["OPENAI_COMPAT2_API_KEY"],
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


def _compat2_base_url() -> str | None:
    return os.environ.get("OPENAI_COMPAT2_BASE_URL") or None


# Per-gateway config so the two OpenAI-compatible providers share one caller.
def _compat_cfg(provider: str):
    if provider == "openai_compatible2":
        return _key_for(provider), _compat2_base_url(), DEFAULT_MODELS["openai_compatible2"]
    return _key_for("openai_compatible"), _compat_base_url(), DEFAULT_MODELS["openai_compatible"]


# Order tried when LLM_PROVIDER=auto.
_AUTO_ORDER = ["anthropic", "openai", "gemini", "openai_compatible", "openai_compatible2"]


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


# Asset-class-specific system prompts. Equities have earnings/valuation; a gold
# future, an FX pair, or a token do NOT — so each class is judged on its own
# native scorecard. The OUTPUT schema (StockAssessment) is identical across all
# classes; only the framing and what-to-weigh guidance changes.
_COMPLIANCE = """COMPLIANCE (non-negotiable): You are NOT a licensed financial advisor and this is NOT \
financial advice or a personalized recommendation. Your output is for EDUCATIONAL and RESEARCH \
purposes only. Always populate the `disclaimer` field with this statement, and never tell the user \
to buy or sell — frame everything as an informational assessment of what the data suggests."""

_JSON_SHAPE = """Respond with a single JSON object only — no markdown, no prose outside the JSON — \
matching exactly this shape:
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

COMMODITY_PROMPT = f"""You are a commodities strategist. Given a snapshot of price action, technical \
indicators, and macro context for a single commodity (a future such as gold, silver, crude oil, or \
natural gas), produce a concise, structured read on whether it looks like a BUY, SELL, or HOLD \
candidate over the upcoming trading days (roughly a 1-4 week horizon).

A commodity has NO earnings, P/E, or analyst price target — do not ask for or invent them. Weigh \
the inputs that actually drive commodities:
- Technicals & trend. 50d vs 200d SMA, price vs those averages, RSI (>70 extended / <30 oversold), \
MACD. Commodities trend and mean-revert violently — look for confluence, call out conflicts.
- Macro context. The US dollar (DXY) is an inverse headwind/tailwind for dollar-priced commodities; \
real yields (10Y UST) are the key driver for non-yielding stores of value like gold; VIX proxies \
risk appetite. The gold/silver ratio is a relative-value gauge for precious metals.
- Volatility & range. Realized volatility (sizing/risk) and where price sits in its trailing range \
(near highs = chase risk; near lows = potential contrarian value).
- Seasonality. If provided, treat the current month's historical bias as a soft, physically-driven \
tilt — not a guarantee.
- Positioning (COT) and inventories, if provided. Extreme large-spec positioning is a contrarian \
flag; inventory builds/draws are direct supply-demand signals.

Rules:
- Base every statement on the data provided. If a field is missing ("n/a"), say so rather than \
guessing. Where a normally-important input (e.g. cost of production / AISC, full futures curve) is \
absent, you may note its absence qualitatively but do not fabricate values.
- Always surface both bullish and bearish factors. Set risk_level honestly from volatility, range \
position, and conflicting signals.
- Keep prose tight and plain-English.

{_COMPLIANCE}

{_JSON_SHAPE}"""

FX_PROMPT = f"""You are an FX strategist. Given a snapshot of price action, technical indicators, and \
macro context for a single currency pair, produce a concise, structured read on whether the pair \
looks like a BUY, SELL, or HOLD candidate (i.e. whether the BASE currency strengthens vs the QUOTE \
currency) over the upcoming trading days (roughly a 1-4 week horizon).

A currency is a relative claim on an economy and its interest rate — there are NO earnings, P/E, or \
analyst targets. Do not ask for or invent them. Weigh what actually drives FX:
- Interest-rate differential. The single biggest driver — capital flows to the higher (real) yield \
(the carry trade). Use the policy-rate differential if provided; otherwise reason from US yields and \
the dollar.
- The US dollar (DXY). Broad dollar strength/weakness sets the tide for most pairs.
- Technicals & trend. 50d vs 200d SMA, price vs averages, RSI, MACD — FX trends cleanly but reverses \
on central-bank surprises.
- Volatility & range. Realized vol for sizing; range position for stretch.
- Positioning (CFTC IMM / COT), if provided — crowded positioning is an asymmetric reversal risk.
- Risk sentiment. Note safe-haven dynamics (USD, JPY, CHF strengthen in risk-off) where relevant.

Rules:
- Base every statement on the data provided. If a field is missing ("n/a"), say so. Do not fabricate \
macro releases, central-bank decisions, or numbers not in the snapshot.
- Always surface both bullish and bearish factors. Set risk_level honestly.
- Frame the signal as a view on the BASE currency vs the QUOTE currency. Keep prose tight.

{_COMPLIANCE}

{_JSON_SHAPE}"""

CRYPTO_PROMPT = f"""You are a digital-asset strategist. Given a snapshot of price action, technical \
indicators, and on-chain/market context for a single cryptocurrency, produce a concise, structured \
read on whether it looks like a BUY, SELL, or HOLD candidate over the upcoming trading days (roughly \
a 1-4 week horizon).

Crypto has no earnings or P/E in the equity sense — judge it on its native scorecard:
- Network & supply. Market cap, circulating vs max supply (how much is already minted; remaining \
emission is future sell pressure), and BTC dominance (capital rotation between BTC and alts).
- Derivatives & leverage. The funding rate is the best short-term sentiment/positioning gauge \
(persistently high positive funding = crowded, over-leveraged longs primed for a squeeze); open \
interest shows how much leverage is in the system.
- Sentiment. The Fear & Greed index — extremes are contrarian.
- Technicals & trend. 50d vs 200d SMA, RSI, MACD. Crypto trends hard and is highly volatile.
- Volatility & range. Realized vol is large here — central to sizing; range position shows stretch.
- Macro. Be honest that crypto often trades as a high-beta risk asset / liquidity proxy.

Rules:
- Base every statement on the data provided. If a field is missing ("n/a"), say so. Do not fabricate \
on-chain figures (MVRV, NVT, SOPR, exchange flows) that aren't in the snapshot — note them as \
unavailable if relevant rather than inventing them.
- Always surface both bullish and bearish factors. Set risk_level honestly (crypto skews higher).
- Keep prose tight and plain-English.

{_COMPLIANCE}

{_JSON_SHAPE}"""


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
    """Return the single primary provider to use, or None if none is configured."""
    chain = resolve_provider_chain()
    return chain[0] if chain else None


def resolve_provider_chain() -> list[str]:
    """Ordered list of configured providers to try, for cross-provider failover.

    Order of precedence:
      1. ``LLM_PROVIDER_CHAIN`` — explicit, comma-separated (e.g. "gemini,openai_compatible").
      2. ``LLM_PROVIDER`` — a single provider is honored as the first/only choice.
      3. Otherwise ``_AUTO_ORDER``.

    In every case only providers that actually have an API key configured are kept,
    in the requested order. ``openai_compatible`` covers Groq/OpenRouter/Mistral/etc.
    """
    chain_env = (os.environ.get("LLM_PROVIDER_CHAIN") or "").strip()
    requested = (os.environ.get("LLM_PROVIDER") or "auto").strip().lower()

    if chain_env:
        order = [p.strip().lower() for p in chain_env.split(",") if p.strip()]
    elif requested in _PROVIDER_KEYS:
        # Honor the explicit primary, then let the rest of the auto-order act as
        # automatic backups (so e.g. LLM_PROVIDER=gemini still falls back to Groq).
        order = [requested, *[p for p in _AUTO_ORDER if p != requested]]
    else:
        order = list(_AUTO_ORDER)

    seen: set[str] = set()
    out: list[str] = []
    for provider in order:
        if provider in _PROVIDER_KEYS and provider not in seen and _key_for(provider):
            seen.add(provider)
            out.append(provider)
    return out


def is_configured() -> bool:
    return resolve_provider() is not None


def active_model(provider: str | None = None) -> str | None:
    """The provider's first-choice model (its strongest configured model)."""
    provider = provider or resolve_provider()
    if not provider:
        return None
    models = models_for(provider)
    return models[0] if models else None


# --------------------------------------------------------------------------- #
# Model fallback lists (strongest first) + quota cooldown
# --------------------------------------------------------------------------- #
# Each provider can list several models (GEMINI_MODELS, OPENAI_COMPAT_MODELS,
# OPENAI_COMPAT2_MODELS — comma-separated, strongest first). They're tried in
# order until one works, so the best model is used while its free quota lasts
# and requests step down instead of failing. A model refused for quota (or one
# that's retired / not available to this key) is skipped for a while, so a
# used-up model doesn't add a wasted round-trip to every request.

def _env_list(name: str) -> list[str]:
    return [m.strip() for m in (os.environ.get(name) or "").split(",") if m.strip()]


def models_for(provider: str) -> list[str]:
    """Ordered, de-duplicated model list for a provider (strongest first)."""
    if provider == "gemini":
        ids = _gemini_candidates()
    elif provider == "openai_compatible":
        ids = _env_list("OPENAI_COMPAT_MODELS") or [DEFAULT_MODELS["openai_compatible"]]
    elif provider == "openai_compatible2":
        ids = _env_list("OPENAI_COMPAT2_MODELS") or [DEFAULT_MODELS["openai_compatible2"]]
    else:
        ids = [DEFAULT_MODELS.get(provider) or ""]
    out: list[str] = []
    for m in ids:
        if m and m not in out:
            out.append(m)
    return out


def _error_kind(exc: Exception) -> str | None:
    """Classify a provider error so the right cooldown applies:
    'unavailable' (retired / no access), 'no-free-tier', 'per-day',
    'per-minute', or None for anything else."""
    s = str(exc).lower()
    if any(k in s for k in ("model_not_found", "does not exist", "archived", "is not found")):
        return "unavailable"
    if any(k in s for k in ("429", "quota", "rate limit", "rate_limit",
                            "resource_exhausted", "resource exhausted", "too many requests")):
        if "limit: 0" in s or "limit 0," in s:
            return "no-free-tier"
        if "day" in s:
            return "per-day"
        return "per-minute"
    return None


_COOLDOWN_SECONDS = {
    "unavailable": 6 * 3600,
    "no-free-tier": 6 * 3600,
    "per-day": 3600,
    "per-minute": 60,
}
_COOLDOWN: dict[tuple[str, str], float] = {}


def _try_models(provider: str, attempt):
    """Call attempt(model) for each of the provider's models, strongest first.
    Returns (result, model_used); raises RuntimeError if every model fails."""
    models = models_for(provider)
    if not models:
        raise RuntimeError(f"{provider}: no model configured")
    errors: list[str] = []
    for model in models:
        if _COOLDOWN.get((provider, model), 0.0) > time.time():
            errors.append(f"{model}: skipped (cooling down after a quota/availability error)")
            continue
        try:
            return attempt(model), model
        except Exception as exc:
            kind = _error_kind(exc)
            if kind:
                _COOLDOWN[(provider, model)] = time.time() + _COOLDOWN_SECONDS[kind]
            errors.append(f"{model}: {str(exc)[:160]}")
    raise RuntimeError(f"all {provider} models failed — " + " | ".join(errors[-4:]))


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


def _price_tech_block(snap: dict) -> str:
    """Shared PRICE + TECHNICALS section (identical across asset classes)."""
    q, i = snap["quote"], snap["indicators"]
    return f"""PRICE
  Last price: {_fmt(q.get('price'))}   Day change: {_fmt(q.get('day_change_pct'))}%
  52-week range: {_fmt(q.get('fifty_two_week_low'))} - {_fmt(q.get('fifty_two_week_high'))}

TECHNICALS
  50-day SMA: {_fmt(i.get('sma50'))}   200-day SMA: {_fmt(i.get('sma200'))}
  Price vs 50d: {_fmt(i.get('price_vs_sma50_pct'))}%   Price vs 200d: {_fmt(i.get('price_vs_sma200_pct'))}%
  Trend: {i.get('trend') or 'n/a'}
  RSI(14): {_fmt(i.get('rsi'))} ({i.get('rsi_zone') or 'n/a'})
  MACD: {_fmt(i.get('macd'))}  signal: {_fmt(i.get('macd_signal'))}  -> {i.get('macd_state') or 'n/a'}"""


def _build_commodity_message(snap: dict) -> str:
    m, c = snap["meta"], snap.get("context", {}) or {}
    seas = c.get("seasonality") or {}
    cot = c.get("cot") or {}
    inv = c.get("inventory") or {}
    lines = [
        "Analyze this commodity for the upcoming trading days.",
        "",
        "INSTRUMENT",
        f"  Ticker: {m['ticker']}   Name: {m.get('name')}   Currency: {m.get('currency', 'USD')}",
        "",
        _price_tech_block(snap),
        "",
        "MACRO CONTEXT",
        f"  US Dollar Index (DXY): {_fmt(c.get('dxy'))}   10Y UST yield: {_fmt(c.get('ust10y'))}   VIX: {_fmt(c.get('vix'))}",
        f"  Gold/Silver ratio: {_fmt(c.get('gold_silver_ratio'))}",
        f"  Realized vol (30d, annualized): {_fmt(c.get('realized_vol_30d'), suffix='%')}",
        f"  Position in trailing range: {_fmt(c.get('range_position_pct'), suffix='th pct')}",
    ]
    if seas:
        lines.append(
            f"  Seasonality — {seas.get('current_month')} avg: {_fmt(seas.get('current_month_avg_pct'), suffix='%')}; "
            f"best {seas.get('best_month')} ({_fmt(seas.get('best_avg_pct'), suffix='%')}), "
            f"worst {seas.get('worst_month')} ({_fmt(seas.get('worst_avg_pct'), suffix='%')})"
        )
    if cot:
        lines.append(
            f"  COT (large specs) as of {cot.get('report_date')}: {cot.get('bias')}, "
            f"net {cot.get('noncomm_net')} ({_fmt(cot.get('net_pct_oi'), suffix='% of OI')})"
        )
    if inv:
        lines.append(
            f"  EIA inventory ({inv.get('period')}): {inv.get('value')} {inv.get('units')} "
            f"(WoW {inv.get('wow_change')})"
        )
    lines += ["", "Return your structured educational assessment as a single JSON object."]
    return "\n".join(lines)


def _build_fx_message(snap: dict) -> str:
    m, c = snap["meta"], snap.get("context", {}) or {}
    rates = c.get("policy_rates") or {}
    cot = c.get("cot") or {}
    base, quote = c.get("base"), c.get("quote")
    lines = [
        "Analyze this currency pair for the upcoming trading days.",
        "",
        "INSTRUMENT",
        f"  Ticker: {m['ticker']}   Pair: {base or '?'}/{quote or '?'} "
        f"(signal = view on {base or 'base'} vs {quote or 'quote'})",
        "",
        _price_tech_block(snap),
        "",
        "MACRO CONTEXT",
        f"  US Dollar Index (DXY): {_fmt(c.get('dxy'))}   10Y UST yield: {_fmt(c.get('us10y'))}",
        f"  Realized vol (30d, annualized): {_fmt(c.get('realized_vol_30d'), suffix='%')}",
        f"  Position in trailing range: {_fmt(c.get('range_position_pct'), suffix='th pct')}",
    ]
    if rates:
        lines.append(
            f"  Policy rates — {base}: {_fmt(rates.get('base_rate'), suffix='%')}, "
            f"{quote}: {_fmt(rates.get('quote_rate'), suffix='%')}, "
            f"differential: {_fmt(rates.get('differential'), suffix='%')}"
        )
    if cot:
        lines.append(
            f"  COT (large specs) as of {cot.get('report_date')}: {cot.get('bias')}, "
            f"net {cot.get('noncomm_net')} ({_fmt(cot.get('net_pct_oi'), suffix='% of OI')})"
        )
    lines += ["", "Return your structured educational assessment as a single JSON object."]
    return "\n".join(lines)


def _build_crypto_message(snap: dict) -> str:
    m, c = snap["meta"], snap.get("context", {}) or {}
    fng = c.get("fear_greed") or {}
    lines = [
        "Analyze this cryptocurrency for the upcoming trading days.",
        "",
        "INSTRUMENT",
        f"  Ticker: {m['ticker']}   Name: {m.get('name')}   Quote currency: {m.get('currency', 'USD')}",
        "",
        _price_tech_block(snap),
        "",
        "NETWORK / MARKET CONTEXT",
        f"  Market cap: {_fmt(c.get('market_cap'))}   BTC dominance: {_fmt(c.get('btc_dominance'), suffix='%')}",
        f"  Circulating supply: {_fmt(c.get('circulating_supply'))}   Max supply: {_fmt(c.get('max_supply'))}   "
        f"Minted: {_fmt(c.get('supply_pct'), suffix='%')}",
        f"  Funding rate (perp): {_fmt(c.get('funding_rate_pct'), suffix='%')}   "
        f"Open interest: {_fmt(c.get('open_interest'))}",
        f"  Fear & Greed: {fng.get('value', 'n/a')} ({fng.get('label', 'n/a')})",
        f"  Realized vol (30d, annualized): {_fmt(c.get('realized_vol_30d'), suffix='%')}",
        f"  Position in trailing range: {_fmt(c.get('range_position_pct'), suffix='th pct')}   "
        f"From ATH: {_fmt(c.get('ath_change_pct'), suffix='%')}",
        "",
        "Return your structured educational assessment as a single JSON object.",
    ]
    return "\n".join(lines)


# Asset class -> (system prompt, user-message builder). Equity is the default.
_PROMPTS_BY_CLASS = {
    "equity": (SYSTEM_PROMPT, _build_user_message),
    "commodity": (COMMODITY_PROMPT, _build_commodity_message),
    "fx": (FX_PROMPT, _build_fx_message),
    "crypto": (CRYPTO_PROMPT, _build_crypto_message),
}


def _select_prompt(snap: dict):
    """Return (system_prompt, user_message) for the snapshot's asset class.
    Indexes/funds fall through to the equity treatment."""
    cls = (snap.get("meta", {}) or {}).get("asset_class", "equity")
    system, builder = _PROMPTS_BY_CLASS.get(cls, _PROMPTS_BY_CLASS["equity"])
    return system, builder(snap)


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def _strip_thinking(text: str | None) -> str:
    """Remove a reasoning model's visible thinking (Qwen-style <think>…</think>).

    If a <think> block was never closed (the answer was cut off mid-thought),
    nothing usable is left: return "" so the caller moves on to the next model."""
    text = _THINK_BLOCK.sub("", text or "")
    low = text.lower()
    if "<think>" in low:
        text = text[: low.index("<think>")]
    return text.strip()


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of model text, tolerating markdown fences and a
    reasoning model's <think> block."""
    text = _strip_thinking(text)
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


def _gemini_complete(model_name: str, system: str, contents, json_mode: bool) -> str:
    """One Gemini (or Gemma) call with one model; returns the raw reply text."""
    import google.generativeai as genai  # lazy import

    genai.configure(api_key=_key_for("gemini"))
    is_gemma = model_name.lower().startswith("gemma")
    config = {"response_mime_type": "application/json"} if (json_mode and not is_gemma) else None
    if is_gemma:
        # Gemma on the Gemini API takes no separate system instruction (and no
        # JSON mode): put the instructions at the start of the first user turn.
        model = genai.GenerativeModel(model_name=model_name, generation_config=config)
        contents = _inline_system(system, contents)
    else:
        model = genai.GenerativeModel(
            model_name=model_name, system_instruction=system, generation_config=config
        )
    return model.generate_content(contents).text or ""


def _inline_system(system: str, contents):
    if isinstance(contents, str):
        return f"{system}\n\n{contents}"
    out = [dict(c) for c in contents]
    for c in out:
        if c.get("role") == "user":
            c["parts"] = [f"{system}\n\n{c['parts'][0]}", *c["parts"][1:]]
            break
    return out


def _compat_complete(provider: str, model: str, messages: list, max_tokens: int, json_mode: bool) -> str:
    """One call to an OpenAI-compatible gateway (Groq, Cerebras, OpenRouter,
    Mistral, LiteLLM…) with one model; returns the raw reply text."""
    from openai import OpenAI  # lazy import

    key, base_url, _ = _compat_cfg(provider)
    if not base_url:
        raise RuntimeError(f"{provider} needs its BASE_URL set (e.g. https://api.cerebras.ai/v1)")
    # No SDK retries: on any failure we move straight on to the next model.
    client = OpenAI(api_key=key, base_url=base_url, max_retries=0, timeout=90)
    kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if "groq.com" in base_url and "qwen" in model.lower():
        # Groq returns Qwen's thinking inside the answer unless told to hide it.
        kwargs["extra_body"] = {"reasoning_format": "hidden"}
    if not json_mode:
        resp = client.chat.completions.create(**kwargs)
    else:
        try:
            resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception as exc:
            if _error_kind(exc):
                raise  # quota / unavailable: don't spend a second call on this model
            # Some gateways reject response_format: retry once without it (the
            # prompt already asks for JSON only, and _extract_json is lenient).
            resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _validated(text: str) -> dict:
    """Parse and schema-check an assessment. Raises if invalid, so the caller
    moves on to the next model instead of returning a broken result."""
    return StockAssessment(**_extract_json(text)).model_dump()


# Reasoning models (gpt-oss, Qwen, Gemini 3.x) spend tokens thinking before they
# answer, so the gateways get more room than the old 2000/1200 limits.
_ANALYSIS_MAX_TOKENS = 4000
_CHAT_MAX_TOKENS = 2500


def _call_anthropic(user_msg: str, system: str = SYSTEM_PROMPT):
    import anthropic  # lazy import

    client = anthropic.Anthropic(api_key=_key_for("anthropic"))
    resp = client.messages.create(
        model=DEFAULT_MODELS["anthropic"],
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
    return _validated(text), DEFAULT_MODELS["anthropic"]


def _call_openai(user_msg: str, system: str = SYSTEM_PROMPT):
    from openai import OpenAI  # lazy import

    client = OpenAI(api_key=_key_for("openai"))
    resp = client.chat.completions.create(
        model=DEFAULT_MODELS["openai"],
        max_tokens=2000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    )
    return _validated(resp.choices[0].message.content), DEFAULT_MODELS["openai"]


def _call_gemini(user_msg: str, system: str = SYSTEM_PROMPT):
    return _try_models("gemini", lambda m: _validated(_gemini_complete(m, system, user_msg, True)))


def _call_compat(user_msg: str, provider: str, system: str = SYSTEM_PROMPT):
    """Any OpenAI-compatible /v1 gateway (``openai_compatible`` or
    ``openai_compatible2``), trying its model list strongest first."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]
    return _try_models(
        provider,
        lambda m: _validated(_compat_complete(provider, m, msgs, _ANALYSIS_MAX_TOKENS, True)),
    )


def _call_openai_compatible(user_msg: str, system: str = SYSTEM_PROMPT):
    return _call_compat(user_msg, "openai_compatible", system)


def _call_openai_compatible2(user_msg: str, system: str = SYSTEM_PROMPT):
    return _call_compat(user_msg, "openai_compatible2", system)


_DISPATCH = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
    "openai_compatible": _call_openai_compatible,
    "openai_compatible2": _call_openai_compatible2,
}


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #


def analyze(snap: dict) -> dict:
    """Produce a validated assessment, trying each configured provider in turn.

    Cross-provider failover: walks ``resolve_provider_chain()`` (e.g. Gemini -> Groq)
    and returns the first provider that succeeds. Raises RuntimeError only if no
    provider is configured or every provider in the chain fails (callers can then
    degrade to the rule-based engine).
    """
    chain = resolve_provider_chain()
    if not chain:
        raise RuntimeError(
            "No LLM provider configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "GEMINI_API_KEY, or OPENAI_COMPAT_API_KEY (and optionally LLM_PROVIDER / "
            "LLM_PROVIDER_CHAIN)."
        )

    system, user_msg = _select_prompt(snap)
    errors: list[str] = []
    for provider in chain:
        try:
            assessment, used_model = _DISPATCH[provider](user_msg, system)
            result = dict(assessment)            # already schema-validated
            result["engine"] = provider          # e.g. "gemini", "openai_compatible"
            result["model"] = used_model         # the model that actually answered
            return result
        except Exception as exc:
            errors.append(f"{provider}: {exc}")

    raise RuntimeError(
        "All configured LLM providers failed — " + " | ".join(errors)
    )


# --------------------------------------------------------------------------- #
# Conversational Q&A (free-form chat grounded in the snapshot)
# --------------------------------------------------------------------------- #

CHAT_SYSTEM_PROMPT = """You are a quantitative equity-research assistant answering a \
user's questions about a single stock. You are given a compact data snapshot (price, \
technical indicators, fundamentals, and Wall-Street analyst consensus) as context.

Guidelines:
- Ground every claim in the provided snapshot. Do NOT invent news, earnings dates, \
guidance, or numbers that aren't in the data. If something isn't in the snapshot, say \
you don't have that data rather than guessing.
- Be concise and plain-English (a few short paragraphs or bullet points). The reader \
may not be a professional investor.
- Be balanced — surface both supportive and cautionary points where relevant.
- You are NOT a licensed financial advisor; this is EDUCATIONAL/RESEARCH information \
only, not financial advice or a personalized recommendation. Never tell the user to \
buy or sell — frame everything as what the data suggests.
- Respond in plain prose/markdown. Do NOT wrap your answer in JSON."""


def _chat_context_nonequity(snap: dict) -> str:
    """Compact context block for commodity/fx/crypto chat — uses the asset-class
    context instead of equity fundamentals/analyst (which are n/a off-equity)."""
    meta = snap.get("meta", {}) or {}
    quote = snap.get("quote", {}) or {}
    ind = snap.get("indicators", {}) or {}
    c = snap.get("context", {}) or {}
    cls = meta.get("asset_class")
    lines = [
        f"Ticker: {meta.get('ticker')} — {meta.get('name')}  ({cls})",
        f"Price: {_fmt(quote.get('price'))} {meta.get('currency', '')} "
        f"(day change {_fmt(quote.get('day_change_pct'), suffix='%')})",
        f"52-week range: {_fmt(quote.get('fifty_two_week_low'))} - {_fmt(quote.get('fifty_two_week_high'))}",
        "--- Technicals ---",
        f"SMA50: {_fmt(ind.get('sma50'))}, SMA200: {_fmt(ind.get('sma200'))}, trend "
        f"{ind.get('trend') or 'n/a'}, RSI: {_fmt(ind.get('rsi'))}, MACD "
        f"{_fmt(ind.get('macd'))}/{_fmt(ind.get('macd_signal'))}",
        f"--- {cls.upper() if cls else ''} context ---",
    ]
    if cls == "commodity":
        seas = c.get("seasonality") or {}
        lines += [
            f"DXY: {_fmt(c.get('dxy'))}, 10Y UST: {_fmt(c.get('ust10y'))}, VIX: {_fmt(c.get('vix'))}, "
            f"gold/silver ratio: {_fmt(c.get('gold_silver_ratio'))}",
            f"Realized vol (30d): {_fmt(c.get('realized_vol_30d'), suffix='%')}, "
            f"range position: {_fmt(c.get('range_position_pct'), suffix='th pct')}",
        ]
        if seas:
            lines.append(
                f"Seasonality: {seas.get('current_month')} avg "
                f"{_fmt(seas.get('current_month_avg_pct'), suffix='%')} "
                f"(best {seas.get('best_month')}, worst {seas.get('worst_month')})"
            )
        if c.get("cot"):
            cot = c["cot"]
            lines.append(f"COT large specs: {cot.get('bias')} ({_fmt(cot.get('net_pct_oi'), suffix='% of OI')})")
        if c.get("inventory"):
            inv = c["inventory"]
            lines.append(f"EIA inventory {inv.get('period')}: {inv.get('value')} {inv.get('units')} (WoW {inv.get('wow_change')})")
    elif cls == "fx":
        lines += [
            f"Pair: {c.get('base')}/{c.get('quote')}",
            f"DXY: {_fmt(c.get('dxy'))}, 10Y UST: {_fmt(c.get('us10y'))}",
            f"Realized vol (30d): {_fmt(c.get('realized_vol_30d'), suffix='%')}, "
            f"range position: {_fmt(c.get('range_position_pct'), suffix='th pct')}",
        ]
        if c.get("policy_rates"):
            r = c["policy_rates"]
            lines.append(
                f"Policy rates: {c.get('base')} {_fmt(r.get('base_rate'), suffix='%')} / "
                f"{c.get('quote')} {_fmt(r.get('quote_rate'), suffix='%')} "
                f"(diff {_fmt(r.get('differential'), suffix='%')})"
            )
        if c.get("cot"):
            cot = c["cot"]
            lines.append(f"COT large specs: {cot.get('bias')} ({_fmt(cot.get('net_pct_oi'), suffix='% of OI')})")
    elif cls == "crypto":
        fng = c.get("fear_greed") or {}
        lines += [
            f"Market cap: {_fmt(c.get('market_cap'))}, BTC dominance: {_fmt(c.get('btc_dominance'), suffix='%')}",
            f"Supply: {_fmt(c.get('circulating_supply'))} / {_fmt(c.get('max_supply'))} "
            f"({_fmt(c.get('supply_pct'), suffix='%')} minted)",
            f"Funding rate: {_fmt(c.get('funding_rate_pct'), suffix='%')}, "
            f"open interest: {_fmt(c.get('open_interest'))}",
            f"Fear & Greed: {fng.get('value', 'n/a')} ({fng.get('label', 'n/a')})",
            f"Realized vol (30d): {_fmt(c.get('realized_vol_30d'), suffix='%')}, "
            f"range position: {_fmt(c.get('range_position_pct'), suffix='th pct')}",
        ]
    return "\n".join(lines)


def _chat_context(snap: dict) -> str:
    """Render a compact, model-friendly context block from the snapshot."""
    meta = snap.get("meta", {}) or {}
    if meta.get("asset_class") in ("commodity", "fx", "crypto") and snap.get("context"):
        return _chat_context_nonequity(snap)
    quote = snap.get("quote", {}) or {}
    fund = snap.get("fundamentals", {}) or {}
    an = snap.get("analyst", {}) or {}
    ind = snap.get("indicators", {}) or {}
    lines = [
        f"Ticker: {meta.get('ticker')} — {meta.get('name')}",
        f"Sector/Industry: {_fmt(meta.get('sector'))} / {_fmt(meta.get('industry'))}",
        f"Price: {_fmt(quote.get('price'))} {meta.get('currency', '')} "
        f"(day change {_fmt(quote.get('day_change_pct'), suffix='%')})",
        f"52-week range: {_fmt(quote.get('fifty_two_week_low'))} - "
        f"{_fmt(quote.get('fifty_two_week_high'))}",
        "--- Fundamentals ---",
        f"Market cap: {_fmt(fund.get('market_cap'))}",
        f"Trailing P/E: {_fmt(fund.get('trailing_pe'))}, Forward P/E: "
        f"{_fmt(fund.get('forward_pe'))}, PEG: {_fmt(fund.get('peg_ratio'))}",
        f"Profit margin: {_fmt(fund.get('profit_margin'), pct=True)}, "
        f"Revenue growth: {_fmt(fund.get('revenue_growth'), pct=True)}, "
        f"Earnings growth: {_fmt(fund.get('earnings_growth'), pct=True)}",
        f"Beta: {_fmt(fund.get('beta'))}, Dividend yield: "
        f"{_fmt(fund.get('dividend_yield'), pct=True)}",
        "--- Technicals (latest) ---",
        f"SMA50: {_fmt(ind.get('sma50'))}, SMA200: {_fmt(ind.get('sma200'))}, "
        f"RSI: {_fmt(ind.get('rsi'))}, MACD: {_fmt(ind.get('macd'))} / signal "
        f"{_fmt(ind.get('macd_signal'))}",
        "--- Analyst consensus ---",
        f"Recommendation: {_fmt(an.get('recommendation'))} "
        f"({_fmt(an.get('num_analysts'))} analysts)",
        f"Mean target: {_fmt(an.get('target_mean'))} "
        f"(upside {_fmt(an.get('target_upside_pct'), suffix='%')}), "
        f"low {_fmt(an.get('target_low'))} / high {_fmt(an.get('target_high'))}",
    ]
    return "\n".join(lines)


# Asset-class role note layered on top of the shared chat system prompt so the
# assistant frames answers correctly off-equity (no P/E talk on gold, etc.).
_CHAT_ROLE_NOTE = {
    "commodity": "The instrument is a COMMODITY (a future). It has no earnings, P/E, or analyst "
                 "price target — reason from trend, the dollar (DXY), real yields, volatility, "
                 "range position, seasonality, and positioning. Never ask for valuation multiples.",
    "fx": "The instrument is a CURRENCY PAIR. Frame answers as a view on the base vs the quote "
          "currency, driven by interest-rate differentials, the dollar, and positioning — not "
          "earnings or valuation.",
    "crypto": "The instrument is a CRYPTOCURRENCY. Reason from supply/market cap, BTC dominance, "
              "funding rate and open interest, the Fear & Greed index, and volatility — not "
              "earnings or P/E.",
}


def _chat_messages(snap: dict, question: str, history: list | None = None) -> list:
    """Build the OpenAI-style message list shared by the chat dispatchers."""
    context = _chat_context(snap)
    cls = (snap.get("meta", {}) or {}).get("asset_class", "equity")
    noun = {"commodity": "commodity", "fx": "currency pair", "crypto": "cryptocurrency"}.get(cls, "stock")
    msgs = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    if cls in _CHAT_ROLE_NOTE:
        msgs.append({"role": "system", "content": _CHAT_ROLE_NOTE[cls]})
    msgs.append({
        "role": "system",
        "content": f"Data snapshot for the {noun} in question:\n{context}",
    })
    for turn in (history or [])[-8:]:
        role = turn.get("role")
        text = (turn.get("text") or turn.get("content") or "").strip()
        if not text:
            continue
        msgs.append({"role": "assistant" if role in ("ai", "assistant") else "user", "content": text})
    msgs.append({"role": "user", "content": question})
    return msgs


def _nonempty(text: str | None) -> str:
    """Clean a chat reply (drop visible thinking); raise if nothing is left, so
    the caller moves on to the next model."""
    text = _strip_thinking(text)
    if not text:
        raise RuntimeError("empty reply")
    return text


def _chat_anthropic(messages: list):
    import anthropic  # lazy import

    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    convo = [m for m in messages if m["role"] != "system"]
    client = anthropic.Anthropic(api_key=_key_for("anthropic"))
    resp = client.messages.create(
        model=DEFAULT_MODELS["anthropic"],
        max_tokens=1200,
        system=system,
        messages=[{"role": m["role"], "content": m["content"]} for m in convo],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return _nonempty(text), DEFAULT_MODELS["anthropic"]


def _chat_openai(messages: list):
    from openai import OpenAI  # lazy import

    client = OpenAI(api_key=_key_for("openai"))
    resp = client.chat.completions.create(
        model=DEFAULT_MODELS["openai"], max_tokens=1200, messages=messages
    )
    return _nonempty(resp.choices[0].message.content), DEFAULT_MODELS["openai"]


def _chat_gemini(messages: list):
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    convo = [m for m in messages if m["role"] != "system"]
    parts = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [m["content"]]}
        for m in convo
    ]
    return _try_models("gemini", lambda m: _nonempty(_gemini_complete(m, system, parts, False)))


def _chat_compat(messages: list, provider: str):
    return _try_models(
        provider,
        lambda m: _nonempty(_compat_complete(provider, m, messages, _CHAT_MAX_TOKENS, False)),
    )


def _chat_openai_compatible(messages: list):
    return _chat_compat(messages, "openai_compatible")


def _chat_openai_compatible2(messages: list):
    return _chat_compat(messages, "openai_compatible2")


_CHAT_DISPATCH = {
    "anthropic": _chat_anthropic,
    "openai": _chat_openai,
    "gemini": _chat_gemini,
    "openai_compatible": _chat_openai_compatible,
    "openai_compatible2": _chat_openai_compatible2,
}


def ping(provider: str) -> dict:
    """Minimal liveness check for ONE provider — a 1-token round-trip. Returns
    {ok, model, reply, error}. Used by the /api/diag/llm health endpoint so each
    configured provider (incl. the Cerebras fallback) can be verified in isolation
    without disturbing the production failover order. `model` is the model that
    actually answered (walking the provider's list strongest first)."""
    models = models_for(provider)
    if provider not in _CHAT_DISPATCH:
        return {"ok": False, "model": None, "models": models, "reply": None, "error": "unknown provider"}
    messages = [
        {"role": "system", "content": "You are a health check. Reply with exactly: OK"},
        {"role": "user", "content": "ping"},
    ]
    try:
        reply, used = _CHAT_DISPATCH[provider](messages)
        return {"ok": True, "model": used, "models": models, "reply": reply[:60], "error": None}
    except Exception as exc:
        return {"ok": False, "model": active_model(provider), "models": models,
                "reply": None, "error": str(exc)[:240]}


def list_models(provider: str) -> dict:
    """Model ids this provider's API key can actually use — ids only, never keys.

    Powers /api/diag/models, used to pick a replacement when /api/diag/llm reports
    a retired or inaccessible model (e.g. a 404 "model not found"). For the
    OpenAI-compatible gateways it also reports the host, so it's clear which one
    is Groq and which is Cerebras."""
    from urllib.parse import urlparse

    out: dict = {"configured": bool(_key_for(provider)), "configured_models": models_for(provider)}
    if not out["configured"]:
        return out
    try:
        if provider in ("openai_compatible", "openai_compatible2"):
            from openai import OpenAI  # lazy import

            key, base_url, _ = _compat_cfg(provider)
            out["host"] = urlparse(base_url or "").netloc or None
            if not base_url:
                raise RuntimeError("no BASE_URL set for this gateway")
            ids = [m.id for m in OpenAI(api_key=key, base_url=base_url).models.list()]
        elif provider == "openai":
            from openai import OpenAI  # lazy import

            ids = [m.id for m in OpenAI(api_key=_key_for("openai")).models.list()]
        elif provider == "gemini":
            import google.generativeai as genai  # lazy import

            genai.configure(api_key=_key_for("gemini"))
            ids = [
                m.name.split("/")[-1]
                for m in genai.list_models()
                if "generateContent" in (getattr(m, "supported_generation_methods", None) or [])
            ]
        elif provider == "anthropic":
            import anthropic  # lazy import

            ids = [m.id for m in anthropic.Anthropic(api_key=_key_for("anthropic")).models.list()]
        else:
            raise RuntimeError("unknown provider")
        ids = sorted(set(ids))
        out.update({
            "ok": True,
            "count": len(ids),
            "configured_but_unavailable": [m for m in out["configured_models"] if m not in ids],
            "ids": ids[:100],
        })
    except Exception as exc:
        out.update({"ok": False, "error": str(exc)[:240]})
    return out


def chat(snap: dict, question: str, history: list | None = None) -> dict:
    """Answer a free-form question about the stock, grounded in the snapshot.

    Cross-provider failover: tries each provider in ``resolve_provider_chain()``
    (e.g. Gemini -> Groq) and returns the first that succeeds. Returns
    {"reply": str, "engine": provider, "model": model}. Raises RuntimeError if no
    provider is configured or every provider in the chain fails.
    """
    chain = resolve_provider_chain()
    if not chain:
        raise RuntimeError(
            "No LLM provider configured. Set an API key (and optionally LLM_PROVIDER / "
            "LLM_PROVIDER_CHAIN)."
        )
    messages = _chat_messages(snap, question, history)
    errors: list[str] = []
    for provider in chain:
        try:
            reply, used_model = _CHAT_DISPATCH[provider](messages)
            return {"reply": reply, "engine": provider, "model": used_model}
        except Exception as exc:
            errors.append(f"{provider}: {exc}")

    raise RuntimeError("All configured LLM providers failed — " + " | ".join(errors))


# --------------------------------------------------------------------------- #
# Model trials (/api/diag/trial) — compare models on THIS deployment's keys
# --------------------------------------------------------------------------- #

# Realistic shape, clearly fake values: trials never depend on (or spend) Yahoo calls.
_TRIAL_SNAPSHOT = {
    "meta": {"ticker": "TRIAL", "name": "Trial Corp (synthetic test data)", "sector": "Technology",
             "industry": "Semiconductors", "currency": "USD", "asset_class": "equity"},
    "quote": {"price": 142.5, "day_change_pct": 1.8,
              "fifty_two_week_low": 96.2, "fifty_two_week_high": 158.9},
    "indicators": {"sma50": 135.1, "sma200": 121.4, "price_vs_sma50_pct": 5.5,
                   "price_vs_sma200_pct": 17.4, "trend": "golden-cross (50d above 200d)",
                   "rsi": 66.3, "rsi_zone": "neutral", "macd": 2.41, "macd_signal": 1.97,
                   "macd_state": "bullish (MACD above signal)"},
    "fundamentals": {"market_cap": 3.4e11, "beta": 1.6, "trailing_pe": 38.9, "forward_pe": 29.4,
                     "peg_ratio": 1.4, "revenue_growth": 0.22, "earnings_growth": 0.31,
                     "profit_margin": 0.27, "dividend_yield": 0.004},
    "analyst": {"recommendation": "buy", "recommendation_mean": 1.9, "num_analysts": 41,
                "target_mean": 165.0, "target_low": 120.0, "target_high": 210.0,
                "target_upside_pct": 15.8},
}


def trial(provider: str, models: list[str] | None = None) -> list[dict]:
    """Run the real assessment prompt once per model on synthetic data: does the
    model answer on this key (i.e. is it free here), is the output valid, how long
    does it take, does reasoning text leak. Ignores cooldowns. Every run spends
    real free-tier quota, which is why the endpoint is token-protected."""
    if provider not in ("gemini", "openai_compatible", "openai_compatible2"):
        raise ValueError("trials support gemini, openai_compatible and openai_compatible2")
    if not _key_for(provider):
        raise ValueError(f"{provider} has no API key configured")
    models = models or models_for(provider)
    system, user_msg = _select_prompt(_TRIAL_SNAPSHOT)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]

    def run(model: str) -> dict:
        t0 = time.time()
        try:
            if provider == "gemini":
                raw = _gemini_complete(model, system, user_msg, True)
            else:
                raw = _compat_complete(provider, model, msgs, _ANALYSIS_MAX_TOKENS, True)
            data = _validated(raw)
            return {"model": model, "ok": True, "ms": int((time.time() - t0) * 1000),
                    "thinking_leaked": "<think>" in raw.lower(), "chars": len(raw),
                    "signal": data.get("signal"), "risk_level": data.get("risk_level")}
        except Exception as exc:
            return {"model": model, "ok": False, "ms": int((time.time() - t0) * 1000),
                    "kind": _error_kind(exc), "error": str(exc)[:220]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(run, models))
