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
_GEMINI_FALLBACKS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
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
    last_err: Exception | None = None
    for model_name in _gemini_candidates():
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=SYSTEM_PROMPT,
                generation_config={"response_mime_type": "application/json"},
            )
            resp = model.generate_content(user_msg)
            return _extract_json(resp.text)
        except Exception as exc:  # 404 / not-found / unsupported -> try next id
            last_err = exc
            continue
    raise RuntimeError(
        f"All Gemini models failed ({', '.join(_gemini_candidates())}). "
        f"Last error: {last_err}"
    )


def _call_compat(user_msg: str, provider: str) -> dict:
    """Any OpenAI-compatible /v1 gateway: Groq, OpenRouter, Mistral, Cerebras,
    LiteLLM, or a corporate model gateway. Reuses the openai SDK with a custom
    base_url. Key + URL + model come from the env vars for the given provider
    (``openai_compatible`` or ``openai_compatible2``)."""
    from openai import OpenAI  # lazy import

    key, base_url, model = _compat_cfg(provider)
    if not base_url:
        raise RuntimeError(f"{provider} needs its BASE_URL set (e.g. https://api.cerebras.ai/v1)")
    if not model:
        raise RuntimeError(f"{provider} needs its MODEL set (e.g. llama-3.3-70b)")
    client = OpenAI(api_key=key, base_url=base_url)
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


def _call_openai_compatible(user_msg: str) -> dict:
    return _call_compat(user_msg, "openai_compatible")


def _call_openai_compatible2(user_msg: str) -> dict:
    return _call_compat(user_msg, "openai_compatible2")


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

    user_msg = _build_user_message(snap)
    errors: list[str] = []
    for provider in chain:
        try:
            raw = _DISPATCH[provider](user_msg)
            assessment = StockAssessment(**raw)
            result = assessment.model_dump()
            result["engine"] = provider          # e.g. "gemini", "openai_compatible"
            result["model"] = DEFAULT_MODELS[provider]
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


def _chat_context(snap: dict) -> str:
    """Render a compact, model-friendly context block from the snapshot."""
    meta = snap.get("meta", {}) or {}
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


def _chat_messages(snap: dict, question: str, history: list | None = None) -> list:
    """Build the OpenAI-style message list shared by the chat dispatchers."""
    context = _chat_context(snap)
    msgs = [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": f"Data snapshot for the stock in question:\n{context}",
        },
    ]
    for turn in (history or [])[-8:]:
        role = turn.get("role")
        text = (turn.get("text") or turn.get("content") or "").strip()
        if not text:
            continue
        msgs.append({"role": "assistant" if role in ("ai", "assistant") else "user", "content": text})
    msgs.append({"role": "user", "content": question})
    return msgs


def _chat_anthropic(messages: list) -> str:
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
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def _chat_openai(messages: list) -> str:
    from openai import OpenAI  # lazy import

    client = OpenAI(api_key=_key_for("openai"))
    resp = client.chat.completions.create(
        model=DEFAULT_MODELS["openai"], max_tokens=1200, messages=messages
    )
    return (resp.choices[0].message.content or "").strip()


def _chat_gemini(messages: list) -> str:
    import google.generativeai as genai  # lazy import

    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    genai.configure(api_key=_key_for("gemini"))
    convo = [m for m in messages if m["role"] != "system"]
    parts = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [m["content"]]}
        for m in convo
    ]
    last_err: Exception | None = None
    for model_name in _gemini_candidates():
        try:
            model = genai.GenerativeModel(
                model_name=model_name, system_instruction=system
            )
            resp = model.generate_content(parts)
            return (resp.text or "").strip()
        except Exception as exc:  # 404 / not-found / unsupported -> try next id
            last_err = exc
            continue
    raise RuntimeError(
        f"All Gemini models failed ({', '.join(_gemini_candidates())}). "
        f"Last error: {last_err}"
    )


def _chat_compat(messages: list, provider: str) -> str:
    from openai import OpenAI  # lazy import

    key, base_url, model = _compat_cfg(provider)
    if not base_url or not model:
        raise RuntimeError(f"{provider} chat needs its BASE_URL and MODEL set.")
    client = OpenAI(api_key=key, base_url=base_url)
    resp = client.chat.completions.create(model=model, max_tokens=1200, messages=messages)
    return (resp.choices[0].message.content or "").strip()


def _chat_openai_compatible(messages: list) -> str:
    return _chat_compat(messages, "openai_compatible")


def _chat_openai_compatible2(messages: list) -> str:
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
    without disturbing the production failover order."""
    model = DEFAULT_MODELS.get(provider)
    if provider not in _CHAT_DISPATCH:
        return {"ok": False, "model": model, "reply": None, "error": "unknown provider"}
    messages = [
        {"role": "system", "content": "You are a health check. Reply with exactly: OK"},
        {"role": "user", "content": "ping"},
    ]
    try:
        reply = (_CHAT_DISPATCH[provider](messages) or "").strip()
        return {"ok": bool(reply), "model": model, "reply": reply[:60], "error": None}
    except Exception as exc:
        return {"ok": False, "model": model, "reply": None, "error": str(exc)[:240]}


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
            reply = _CHAT_DISPATCH[provider](messages)
            if reply and reply.strip():
                return {
                    "reply": reply,
                    "engine": provider,
                    "model": DEFAULT_MODELS[provider],
                }
            errors.append(f"{provider}: empty reply")
        except Exception as exc:
            errors.append(f"{provider}: {exc}")

    raise RuntimeError("All configured LLM providers failed — " + " | ".join(errors))
