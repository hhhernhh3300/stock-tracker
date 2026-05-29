"""
Daily breakout screener.

Pulls 1y of OHLCV for each ticker in data/universe.txt via yfinance,
computes three signal sets, and writes data/screener.json.

Signals:
  - 52-week-high breakouts: within NEAR_HIGH_PCT of trailing-252-day max,
    with positive 1-month return.
  - Volume surge: today's volume >= VOL_SURGE_MULT * mean(last 20 days vol).
  - Top relative strength: best 3-month return vs SPY, top RS_TOP_N.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = ROOT / "data" / "universe.txt"
OUTPUT_FILE = ROOT / "data" / "screener.json"

# Thresholds — tune here.
NEAR_HIGH_PCT = 2.0      # within 2% of 52w high
VOL_SURGE_MULT = 2.0     # 2x 20-day avg volume
RS_TOP_N = 25            # top N by relative strength
BREAKOUT_MIN_MONTH_PCT = 0.0  # require positive 1m return for breakout candidates
BENCH = "SPY"


def load_universe() -> list[str]:
    syms = []
    for line in UNIVERSE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        syms.append(line)
    # Dedupe but preserve order
    seen = set()
    out = []
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def fetch_history(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Fetch ~1y of daily OHLCV for each symbol. Returns dict ticker -> DataFrame."""
    print(f"Fetching {len(symbols)} tickers from Yahoo…", flush=True)
    # yfinance batch download is much faster than per-ticker
    df = yf.download(
        tickers=symbols,
        period="1y",
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )
    out = {}
    for sym in symbols:
        try:
            if len(symbols) == 1:
                sub = df
            else:
                sub = df[sym]
            sub = sub.dropna(how="all")
            if len(sub) < 30:
                continue
            out[sym] = sub
        except (KeyError, AttributeError):
            continue
    print(f"Got data for {len(out)}/{len(symbols)} tickers.", flush=True)
    return out


def get_name(symbol: str) -> str:
    """Best-effort short name. Network call, kept minimal."""
    try:
        info = yf.Ticker(symbol).info
        return info.get("shortName") or info.get("longName") or ""
    except Exception:
        return ""


def pct_change(a: float, b: float) -> float | None:
    if a is None or b is None or b == 0 or math.isnan(a) or math.isnan(b):
        return None
    return (a - b) / b * 100.0


def compute_signals(data: dict[str, pd.DataFrame]) -> dict:
    bench_df = data.get(BENCH)
    if bench_df is None or len(bench_df) < 65:
        print(f"WARNING: {BENCH} benchmark data missing/short — RS section will be empty.", flush=True)
        spy_3m = None
    else:
        spy_close = bench_df["Close"].dropna()
        spy_3m = pct_change(float(spy_close.iloc[-1]), float(spy_close.iloc[-min(63, len(spy_close))]))

    breakouts = []
    vol_surges = []
    rs_rows = []

    for sym, df in data.items():
        if sym == BENCH:
            continue
        closes = df["Close"].dropna()
        vols = df["Volume"].dropna()
        if len(closes) < 30 or len(vols) < 21:
            continue

        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
        day_pct = pct_change(last, prev) if prev else None

        # 52-week-high check
        window = closes.iloc[-min(252, len(closes)):]
        high_52 = float(window.max())
        from_high_pct = pct_change(last, high_52)  # negative or 0 if at high

        month_ago = closes.iloc[-min(22, len(closes))]
        month_pct = pct_change(last, float(month_ago))

        if from_high_pct is not None and from_high_pct >= -NEAR_HIGH_PCT and (month_pct or 0) > BREAKOUT_MIN_MONTH_PCT:
            breakouts.append({
                "symbol": sym,
                "price": round(last, 2),
                "day_pct": round(day_pct, 2) if day_pct is not None else None,
                "from_high_pct": round(from_high_pct, 2),
                "month_pct": round(month_pct, 2) if month_pct is not None else None,
            })

        # Volume surge
        today_vol = float(vols.iloc[-1])
        avg20 = float(vols.iloc[-21:-1].mean()) if len(vols) >= 21 else None
        if avg20 and avg20 > 0:
            ratio = today_vol / avg20
            if ratio >= VOL_SURGE_MULT:
                vol_surges.append({
                    "symbol": sym,
                    "price": round(last, 2),
                    "day_pct": round(day_pct, 2) if day_pct is not None else None,
                    "vol_ratio": round(ratio, 2),
                    "volume": int(today_vol),
                })

        # 3-month return for RS ranking
        if len(closes) >= 63:
            three_m_ago = float(closes.iloc[-63])
            three_m_pct = pct_change(last, three_m_ago)
            rs_rows.append({
                "symbol": sym,
                "price": round(last, 2),
                "three_month_pct": round(three_m_pct, 2) if three_m_pct is not None else None,
                "rs_vs_spy": round((three_m_pct or 0) - (spy_3m or 0), 2) if spy_3m is not None else None,
            })

    # Sort
    breakouts.sort(key=lambda r: r["from_high_pct"], reverse=True)
    vol_surges.sort(key=lambda r: r["vol_ratio"], reverse=True)
    rs_rows.sort(key=lambda r: r["three_month_pct"] or -999, reverse=True)
    top_rs = rs_rows[:RS_TOP_N]

    # Names — only for rows we'll display, to limit API calls
    display_syms = set()
    for r in breakouts + vol_surges + top_rs:
        display_syms.add(r["symbol"])
    print(f"Looking up names for {len(display_syms)} displayed tickers…", flush=True)
    name_cache = {}
    for sym in display_syms:
        name_cache[sym] = get_name(sym)
        time.sleep(0.05)  # gentle on Yahoo

    for r in breakouts + vol_surges + top_rs:
        r["name"] = name_cache.get(r["symbol"], "")

    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "universe_size": len(data),
        "spy_3m_pct": round(spy_3m, 2) if spy_3m is not None else None,
        "thresholds": {
            "near_high_pct": NEAR_HIGH_PCT,
            "vol_surge_mult": VOL_SURGE_MULT,
            "breakout_min_month_pct": BREAKOUT_MIN_MONTH_PCT,
        },
        "breakouts": breakouts,
        "volume_surges": vol_surges,
        "top_relative_strength": top_rs,
    }


def main() -> int:
    syms = load_universe()
    if not syms:
        print("Universe is empty.", file=sys.stderr)
        return 1
    if BENCH not in syms:
        syms.append(BENCH)
    data = fetch_history(syms)
    if not data:
        print("No data fetched.", file=sys.stderr)
        return 1
    result = compute_signals(data)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE} — "
          f"{len(result['breakouts'])} breakouts, "
          f"{len(result['volume_surges'])} vol surges, "
          f"{len(result['top_relative_strength'])} RS leaders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
