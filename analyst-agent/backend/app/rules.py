"""FREE rule-based assessment engine — no API call, no charge.

Produces the same StockAssessment shape as the Claude engine (so the frontend
doesn't care which one ran), but the buy/sell/hold call is a transparent,
deterministic score computed from the indicators, valuation, growth, and analyst
consensus already in the snapshot. Use this as the default; the Claude engine
(analyst.py) is an optional upgrade for richer natural-language reasoning.
"""
from __future__ import annotations

DISCLAIMER = (
    "Rule-based score for educational and research purposes only — not financial "
    "advice, not a personalized recommendation, and not from a licensed advisor. "
    "It is one systematic input, not a forecast. Do your own research."
)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def assess(snap: dict) -> dict:
    i = snap.get("indicators", {})
    f = snap.get("fundamentals", {})
    a = snap.get("analyst", {})
    q = snap.get("quote", {})
    m = snap.get("meta", {})

    bull: list[str] = []
    bear: list[str] = []
    score = 0.0  # >0 leans bullish, <0 bearish

    # --- Trend (moving averages) ---
    trend = (i.get("trend") or "").lower()
    if "golden" in trend:
        score += 1.5
        bull.append("Uptrend — the 50-day average is above the 200-day (golden cross).")
    elif "death" in trend:
        score -= 1.5
        bear.append("Downtrend — the 50-day average is below the 200-day (death cross).")
    p50 = _f(i.get("price_vs_sma50_pct"))
    if p50 is not None:
        score += 0.5 if p50 > 0 else -0.5
        (bull if p50 > 0 else bear).append(
            f"Price is {p50:+.1f}% versus its 50-day average."
        )
    p200 = _f(i.get("price_vs_sma200_pct"))
    if p200 is not None:
        score += 0.5 if p200 > 0 else -0.5

    # --- Momentum (MACD, RSI) ---
    macd = (i.get("macd_state") or "").lower()
    if "bullish" in macd:
        score += 0.75
        bull.append("MACD is above its signal line (positive momentum).")
    elif "bearish" in macd:
        score -= 0.75
        bear.append("MACD is below its signal line (negative momentum).")
    rsi = _f(i.get("rsi"))
    zone = (i.get("rsi_zone") or "").lower()
    if rsi is not None:
        if zone == "overbought":
            score -= 0.5
            bear.append(f"RSI {rsi:.0f} is overbought (>70) — near-term pullback risk.")
        elif zone == "oversold":
            score += 0.5
            bull.append(f"RSI {rsi:.0f} is oversold (<30) — potential mean-reversion bounce.")

    # --- Analyst consensus ---
    rec = (a.get("recommendation") or "").replace("_", " ")
    mean = _f(a.get("recommendation_mean"))  # 1 = strong buy ... 5 = strong sell
    if mean is not None:
        if mean <= 2.0:
            score += 1.0
            bull.append(f"Analyst consensus is bullish ({rec or 'buy'}, mean {mean}).")
        elif mean >= 3.5:
            score -= 1.0
            bear.append(f"Analyst consensus is cautious ({rec or 'hold/sell'}, mean {mean}).")
    upside = _f(a.get("target_upside_pct"))
    if upside is not None:
        if upside >= 15:
            score += 1.0
            bull.append(f"Mean analyst price target implies {upside:+.0f}% upside.")
        elif upside <= 0:
            score -= 1.0
            bear.append(f"Price is above the mean analyst target ({upside:+.0f}%).")
        else:
            bull.append(f"Mean analyst price target implies modest {upside:+.0f}% upside.")

    # --- Fundamentals (light touch) ---
    rg = _f(f.get("revenue_growth"))
    if rg is not None and rg > 0.15:
        score += 0.5
        bull.append(f"Revenue growing ~{rg * 100:.0f}% year over year.")
    eg = _f(f.get("earnings_growth"))
    if eg is not None and eg < 0:
        score -= 0.5
        bear.append("Earnings are contracting year over year.")
    peg = _f(f.get("peg_ratio"))
    if peg is not None and peg > 0:
        if peg <= 1.2:
            score += 0.3
            bull.append(f"Reasonable growth-adjusted valuation (PEG {peg}).")
        elif peg > 3:
            score -= 0.3
            bear.append(f"Rich growth-adjusted valuation (PEG {peg}).")

    # --- Map score to signal + conviction ---
    if score >= 1.5:
        signal = "buy"
    elif score <= -1.5:
        signal = "sell"
    else:
        signal = "hold"
    conviction = "high" if abs(score) >= 3 else "medium" if abs(score) >= 1.5 else "low"

    # --- Risk level ---
    risk = 0
    beta = _f(f.get("beta"))
    if beta is not None:
        risk += 2 if beta >= 1.5 else 1 if beta >= 1.1 else 0
    if zone == "overbought":
        risk += 1
    hi, lo, px = _f(q.get("fifty_two_week_high")), _f(q.get("fifty_two_week_low")), _f(q.get("price"))
    if hi and lo and px and hi > lo and (px - lo) / (hi - lo) >= 0.9:
        risk += 1  # extended near 52-week highs
    pe = _f(f.get("trailing_pe"))
    if pe is not None and pe > 50:
        risk += 1
    if "golden" in trend and "bearish" in macd:
        risk += 1  # trend vs. momentum conflict
    risk_level = "low" if risk <= 0 else "moderate" if risk <= 2 else "high" if risk <= 4 else "very high"

    if not bull:
        bull = ["No clearly bullish signals in the current data."]
    if not bear:
        bear = ["No clearly bearish signals in the current data."]

    name = m.get("name") or m.get("ticker") or "This stock"
    tr_txt = (
        "an uptrend (50-day above 200-day)" if "golden" in trend
        else "a downtrend (50-day below 200-day)" if "death" in trend
        else "a mixed trend"
    )
    rsi_txt = f"RSI is {rsi:.0f} ({zone or 'n/a'})" if rsi is not None else "RSI is unavailable"
    macd_txt = (
        "MACD momentum is positive" if "bullish" in macd
        else "MACD momentum is negative" if "bearish" in macd
        else "MACD momentum is flat"
    )

    lead = bull[0] if (signal == "buy") else bear[0] if (signal == "sell") else (bull[0] if score >= 0 else bear[0])

    return {
        "engine": "rules",
        "signal": signal,
        "conviction": conviction,
        "risk_level": risk_level,
        "time_horizon": "Roughly the next 1–4 weeks (short-to-medium term).",
        "summary": (
            f"Rule-based read: {name} scores as a {signal.upper()} candidate over the next "
            f"~1–4 weeks ({conviction} conviction, {risk_level} risk). Key driver: {lead}"
        ),
        "bullish_factors": bull,
        "bearish_factors": bear,
        "technical_read": f"The price structure shows {tr_txt}; {rsi_txt}; {macd_txt}.",
        "reasoning": (
            f"Weighing {len(bull)} supportive against {len(bear)} cautionary factor(s), the net "
            f"score is {score:+.1f}, which maps to {signal.upper()} with {conviction} conviction and "
            f"{risk_level} risk. This is a transparent local calculation (no AI/LLM, no API charge) — "
            f"treat it as one systematic input alongside your own research."
        ),
        "disclaimer": DISCLAIMER,
    }
