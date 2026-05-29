"""
Daily breakout screener + composite Top Picks + after-hours surge detector.

Modes (passed as --mode):
  daily        - default. Computes breakouts, volume surges, top RS, top picks.
                 Writes data/screener.json.
  after-hours  - Compares latest extended-hours bar to today's regular close.
                 Writes data/after_hours.json.

Signals (daily):
  - 52-week-high breakouts: within NEAR_HIGH_PCT of trailing-252-day max,
    with positive 1-month return.
  - Volume surge: today's volume >= VOL_SURGE_MULT * mean(last 20 days vol).
  - Top relative strength: best 3-month return vs SPY, top RS_TOP_N.
  - Top picks (composite): percentile-rank a ticker on 4 normalized signals
    (1M return, distance from 52w high, volume ratio, RS vs SPY), average,
    surface top TOP_PICKS_N. Requires positive 1M and 3M returns.
"""
from __future__ import annotations

import argparse
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
DAILY_OUT = ROOT / "data" / "screener.json"
AFTER_OUT = ROOT / "data" / "after_hours.json"

# Thresholds
NEAR_HIGH_PCT = 2.0
VOL_SURGE_MULT = 2.0
RS_TOP_N = 25
BREAKOUT_MIN_MONTH_PCT = 0.0
TOP_PICKS_N = 10
AH_MOVE_THRESHOLD_PCT = 2.0  # show after-hours movers >= 2% (either direction)
BENCH = "SPY"


def load_universe() -> list[str]:
    syms: list[str] = []
    for line in UNIVERSE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        syms.append(line)
    seen, out = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def get_name_cached(symbol: str, cache: dict[str, str]) -> str:
    if symbol in cache:
        return cache[symbol]
    try:
        info = yf.Ticker(symbol).info
        name = info.get("shortName") or info.get("longName") or ""
    except Exception:
        name = ""
    cache[symbol] = name
    return name


def pct_change(a: float, b: float) -> float | None:
    if a is None or b is None or b == 0 or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return None
    return (a - b) / b * 100.0


# -------------- DAILY MODE --------------

def fetch_daily_history(symbols: list[str]) -> dict[str, pd.DataFrame]:
    print(f"[daily] Fetching {len(symbols)} tickers from Yahoo…", flush=True)
    df = yf.download(
        tickers=symbols, period="1y", interval="1d",
        group_by="ticker", auto_adjust=True, threads=True, progress=False,
    )
    out = {}
    for sym in symbols:
        try:
            sub = df if len(symbols) == 1 else df[sym]
            sub = sub.dropna(how="all")
            if len(sub) >= 30:
                out[sym] = sub
        except (KeyError, AttributeError):
            continue
    print(f"[daily] Got data for {len(out)}/{len(symbols)} tickers.", flush=True)
    return out


def percentile_rank(values: list[float]) -> list[float]:
    """Return percentile (0-100) for each value in input order."""
    if not values:
        return []
    n = len(values)
    indexed = sorted(enumerate(values), key=lambda x: (x[1] is None, x[1]))
    ranks = [0.0] * n
    for rank, (orig_idx, _) in enumerate(indexed):
        ranks[orig_idx] = 100.0 * rank / max(n - 1, 1)
    return ranks


def compute_daily(data: dict[str, pd.DataFrame]) -> dict:
    bench_df = data.get(BENCH)
    if bench_df is None or len(bench_df) < 65:
        print(f"WARNING: {BENCH} missing/short — RS will be empty.", flush=True)
        spy_3m = None
    else:
        spy_close = bench_df["Close"].dropna()
        spy_3m = pct_change(float(spy_close.iloc[-1]), float(spy_close.iloc[-min(63, len(spy_close))]))

    # Build per-ticker raw metrics
    rows = []
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

        window = closes.iloc[-min(252, len(closes)):]
        high_52 = float(window.max())
        from_high_pct = pct_change(last, high_52)

        month_pct = pct_change(last, float(closes.iloc[-min(22, len(closes))]))

        today_vol = float(vols.iloc[-1])
        avg20 = float(vols.iloc[-21:-1].mean()) if len(vols) >= 21 else None
        vol_ratio = (today_vol / avg20) if (avg20 and avg20 > 0) else None

        three_m_pct = pct_change(last, float(closes.iloc[-63])) if len(closes) >= 63 else None
        rs = (three_m_pct - spy_3m) if (three_m_pct is not None and spy_3m is not None) else None

        rows.append({
            "symbol": sym,
            "price": round(last, 2),
            "day_pct": round(day_pct, 2) if day_pct is not None else None,
            "from_high_pct": round(from_high_pct, 2) if from_high_pct is not None else None,
            "month_pct": round(month_pct, 2) if month_pct is not None else None,
            "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
            "volume": int(today_vol),
            "three_month_pct": round(three_m_pct, 2) if three_m_pct is not None else None,
            "rs_vs_spy": round(rs, 2) if rs is not None else None,
        })

    # Signal sets
    breakouts = [
        {k: r[k] for k in ("symbol", "price", "day_pct", "from_high_pct", "month_pct")}
        for r in rows
        if r["from_high_pct"] is not None and r["from_high_pct"] >= -NEAR_HIGH_PCT
        and (r["month_pct"] or 0) > BREAKOUT_MIN_MONTH_PCT
    ]
    breakouts.sort(key=lambda r: r["from_high_pct"], reverse=True)

    vol_surges = [
        {k: r[k] for k in ("symbol", "price", "day_pct", "vol_ratio", "volume")}
        for r in rows
        if r["vol_ratio"] is not None and r["vol_ratio"] >= VOL_SURGE_MULT
    ]
    vol_surges.sort(key=lambda r: r["vol_ratio"], reverse=True)

    rs_rows = [
        {k: r[k] for k in ("symbol", "price", "three_month_pct", "rs_vs_spy")}
        for r in rows if r["three_month_pct"] is not None
    ]
    rs_rows.sort(key=lambda r: r["three_month_pct"], reverse=True)
    top_rs = rs_rows[:RS_TOP_N]

    # Composite Top Picks: percentile-rank each signal across all rows,
    # require positive 1M & 3M returns.
    eligible = [
        r for r in rows
        if r["month_pct"] is not None and r["month_pct"] > 0
        and r["three_month_pct"] is not None and r["three_month_pct"] > 0
        and r["from_high_pct"] is not None
    ]

    mom_p = percentile_rank([r["month_pct"] for r in eligible])
    # Closer to 52w high = better. Invert: -from_high_pct is bigger when closer.
    bk_p = percentile_rank([-(r["from_high_pct"]) for r in eligible])
    vol_p = percentile_rank([(r["vol_ratio"] or 1.0) for r in eligible])
    rs_p = percentile_rank([(r["rs_vs_spy"] or 0.0) for r in eligible])

    top_picks_rows = []
    for i, r in enumerate(eligible):
        score = round((mom_p[i] + bk_p[i] + vol_p[i] + rs_p[i]) / 4.0, 1)
        signals = []
        if mom_p[i] >= 80: signals.append("1m-momentum")
        if bk_p[i] >= 80: signals.append("near-52w-high")
        if vol_p[i] >= 80: signals.append("volume-surge")
        if rs_p[i] >= 80: signals.append("rs-leader")
        top_picks_rows.append({
            "symbol": r["symbol"],
            "price": r["price"],
            "day_pct": r["day_pct"],
            "month_pct": r["month_pct"],
            "rs_vs_spy": r["rs_vs_spy"],
            "from_high_pct": r["from_high_pct"],
            "vol_ratio": r["vol_ratio"],
            "score": score,
            "signals": signals,
        })
    top_picks_rows.sort(key=lambda r: r["score"], reverse=True)
    top_picks = top_picks_rows[:TOP_PICKS_N]

    # Name lookups only for displayed tickers
    display_syms = set()
    for r in breakouts + vol_surges + top_rs + top_picks:
        display_syms.add(r["symbol"])
    print(f"[daily] Looking up names for {len(display_syms)} displayed tickers…", flush=True)
    name_cache: dict[str, str] = {}
    for sym in display_syms:
        get_name_cached(sym, name_cache)
        time.sleep(0.05)
    for r in breakouts + vol_surges + top_rs + top_picks:
        r["name"] = name_cache.get(r["symbol"], "")

    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "universe_size": len(data),
        "spy_3m_pct": round(spy_3m, 2) if spy_3m is not None else None,
        "thresholds": {
            "near_high_pct": NEAR_HIGH_PCT,
            "vol_surge_mult": VOL_SURGE_MULT,
            "breakout_min_month_pct": BREAKOUT_MIN_MONTH_PCT,
            "top_picks_filter": "positive 1M & 3M returns",
        },
        "top_picks": top_picks,
        "breakouts": breakouts,
        "volume_surges": vol_surges,
        "top_relative_strength": top_rs,
    }


# -------------- AFTER-HOURS MODE --------------

def compute_after_hours(symbols: list[str]) -> dict:
    """
    Compare latest extended-hours 30-min bar to today's regular session close.
    Yahoo returns extended bars in the prepost=True intraday feed.
    """
    print(f"[ah] Fetching regular daily closes for {len(symbols)} tickers…", flush=True)
    reg = yf.download(
        tickers=symbols, period="3d", interval="1d",
        group_by="ticker", auto_adjust=False, threads=True, prepost=False, progress=False,
    )
    print(f"[ah] Fetching intraday 30m incl. prepost…", flush=True)
    ext = yf.download(
        tickers=symbols, period="1d", interval="30m",
        group_by="ticker", auto_adjust=False, threads=True, prepost=True, progress=False,
    )

    movers = []
    for sym in symbols:
        try:
            reg_sub = reg if len(symbols) == 1 else reg[sym]
            ext_sub = ext if len(symbols) == 1 else ext[sym]
        except (KeyError, AttributeError):
            continue
        reg_close = reg_sub["Close"].dropna()
        if reg_close.empty:
            continue
        last_reg = float(reg_close.iloc[-1])

        ext_close = ext_sub["Close"].dropna()
        if ext_close.empty:
            continue
        last_ext = float(ext_close.iloc[-1])

        # If the latest extended bar timestamp equals the regular close, there's no AH data yet.
        if abs(last_ext - last_reg) < 1e-9:
            continue

        ah_pct = pct_change(last_ext, last_reg)
        if ah_pct is None or abs(ah_pct) < AH_MOVE_THRESHOLD_PCT:
            continue
        movers.append({
            "symbol": sym,
            "regular_close": round(last_reg, 2),
            "ext_price": round(last_ext, 2),
            "ah_pct": round(ah_pct, 2),
        })

    # Names for movers
    name_cache: dict[str, str] = {}
    for m in movers:
        m["name"] = get_name_cached(m["symbol"], name_cache)
        time.sleep(0.05)

    surges = sorted([m for m in movers if m["ah_pct"] > 0], key=lambda r: r["ah_pct"], reverse=True)
    drops = sorted([m for m in movers if m["ah_pct"] < 0], key=lambda r: r["ah_pct"])

    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "universe_size": len(symbols),
        "threshold_pct": AH_MOVE_THRESHOLD_PCT,
        "surges": surges,
        "drops": drops,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["daily", "after-hours"], default="daily")
    args = p.parse_args()

    syms = load_universe()
    if not syms:
        print("Universe is empty.", file=sys.stderr)
        return 1
    if BENCH not in syms:
        syms.append(BENCH)

    if args.mode == "daily":
        data = fetch_daily_history(syms)
        if not data:
            print("No data fetched.", file=sys.stderr)
            return 1
        result = compute_daily(data)
        DAILY_OUT.parent.mkdir(parents=True, exist_ok=True)
        DAILY_OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(
            f"Wrote {DAILY_OUT} — "
            f"{len(result['top_picks'])} top picks, "
            f"{len(result['breakouts'])} breakouts, "
            f"{len(result['volume_surges'])} vol surges, "
            f"{len(result['top_relative_strength'])} RS leaders."
        )
    else:
        result = compute_after_hours(syms)
        AFTER_OUT.parent.mkdir(parents=True, exist_ok=True)
        AFTER_OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(
            f"Wrote {AFTER_OUT} — "
            f"{len(result['surges'])} AH surges, "
            f"{len(result['drops'])} AH drops."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
