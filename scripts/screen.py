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
SETUP_TOP_N = 10
AH_MOVE_THRESHOLD_PCT = 2.0  # show after-hours movers >= 2% (either direction)
BENCH = "SPY"

# Setup Score blend weights — sum to 1.0
W_TECH = 0.55
W_FUND = 0.35
W_AH = 0.10
EARNINGS_RISK_WINDOW_DAYS = 7  # warn / penalize if earnings inside this window
LONG_TERM_N = 10
LT_POOL_N = 45  # how many RS leaders to pull fundamentals for, for the long-term screen

# SPDR sector ETFs -> sector name, for the weekly sector-leader board.
SECTOR_ETFS = {
    "XLK": "Technology", "XLC": "Communication Svcs", "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples", "XLE": "Energy", "XLF": "Financials",
    "XLV": "Health Care", "XLI": "Industrials", "XLB": "Materials",
    "XLRE": "Real Estate", "XLU": "Utilities",
}

# Industry / thematic ETFs -> label, for a finer "industries & themes" heatmap.
THEME_ETFS = {
    "SMH": "Semiconductors", "IGV": "Software", "CIBR": "Cybersecurity",
    "SKYY": "Cloud", "FDN": "Internet", "BOTZ": "AI & Robotics",
    "ITA": "Aerospace & Defense", "ARKX": "Space", "BLOK": "Blockchain",
    "URA": "Uranium / Nuclear", "XBI": "Biotech", "KRE": "Regional Banks",
    "TAN": "Solar", "GDX": "Gold Miners", "XHB": "Homebuilders", "IYT": "Transports",
}

# Curated analyst long-term ideas (2026 outlooks: UBS, BlackRock, Goldman, Morgan Stanley)
# -> (firms, one-line thesis). Shown verbatim on the dashboard's Analyst Watchlist.
ANALYST_WATCHLIST = {
    "CEG": ("UBS/BlackRock", "Nuclear power for AI data centers"),
    "VST": ("Street", "Independent power producer; AI demand"),
    "TLN": ("Street", "Nuclear/power; data-center deals"),
    "GEV": ("BlackRock", "Grid + nuclear equipment (GE Vernova)"),
    "BWXT": ("Street", "Small modular reactors / nuclear"),
    "VRT": ("Street", "Data-center power & cooling"),
    "ANET": ("UBS", "AI data-center networking"),
    "AMT": ("UBS", "Towers + data centers"),
    "NXT": ("UBS", "Solar trackers (Nextracker)"),
    "LNT": ("UBS", "Utility w/ data-center contracts"),
    "JCI": ("UBS", "Building efficiency / data-center HVAC"),
    "SSNC": ("UBS", "Financial software; efficiency"),
    "AFRM": ("Morgan Stanley", "Fintech / BNPL; AI use"),
    "STX": ("Morgan Stanley", "Storage for AI data"),
    "CRWD": ("Morgan Stanley", "Cybersecurity platform"),
    "PANW": ("Morgan Stanley", "Cybersecurity + CyberArk deal"),
    "NVDA": ("Morgan Stanley", "Core AI compute"),
    "META": ("Morgan Stanley", "AI-driven ad platform"),
    "UNH": ("UBS/Morgan Stanley", "Healthcare; margin recovery"),
    "MU": ("Goldman/UBS", "Memory/HBM; AI supercycle"),
    "PLTR": ("Street/Seeking Alpha", "AI data analytics; ~70% rev growth"),
    "LLY": ("UBS/CFRA", "GLP-1 / longevity leader"),
    "CIEN": ("Seeking Alpha", "Optical networking for AI data centers"),
}


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


# -------------- FUNDAMENTALS --------------

def fetch_fundamentals(symbol: str) -> dict | None:
    """Pull Yahoo fundamentals via yfinance.info. Returns None on failure."""
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        return None

    def _num(key):
        v = info.get(key)
        if v is None:
            return None
        try:
            v = float(v)
            if math.isnan(v) or math.isinf(v):
                return None
            return v
        except (TypeError, ValueError):
            return None

    # Earnings date — yfinance returns a list of upcoming earnings timestamps
    next_earn_iso = None
    days_to_earn = None
    earn = info.get("earningsDate") or info.get("earningsTimestamp")
    try:
        if isinstance(earn, (list, tuple)) and earn:
            ts = earn[0]
            if hasattr(ts, "timestamp"):
                next_earn = datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
            else:
                next_earn = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            next_earn_iso = next_earn.strftime("%Y-%m-%d")
            days_to_earn = (next_earn.date() - datetime.now(timezone.utc).date()).days
    except Exception:
        pass

    return {
        "trailing_pe": _num("trailingPE"),
        "forward_pe": _num("forwardPE"),
        "peg": _num("pegRatio") or _num("trailingPegRatio"),
        "rev_growth": _num("revenueGrowth"),      # YoY, fraction (e.g., 0.21 = +21%)
        "eps_growth": _num("earningsGrowth"),     # YoY, fraction
        "profit_margin": _num("profitMargins"),
        "operating_margin": _num("operatingMargins"),
        "roe": _num("returnOnEquity"),
        "debt_to_equity": _num("debtToEquity"),
        "next_earnings": next_earn_iso,
        "days_to_earnings": days_to_earn,
        "market_cap": _num("marketCap"),
    }


def score_fundamentals(f: dict) -> tuple[float | None, dict]:
    """Return (composite 0-100, sub-scores dict). None if insufficient data."""
    if not f:
        return None, {}
    subs = {}
    # Revenue growth: 20% YoY = 100, 0% = 0, -10% = 0
    if f.get("rev_growth") is not None:
        subs["rev_growth"] = max(0.0, min(100.0, f["rev_growth"] * 500.0))
    # EPS growth: 40% YoY = 100, capped
    if f.get("eps_growth") is not None:
        subs["eps_growth"] = max(0.0, min(100.0, f["eps_growth"] * 250.0))
    # Profit margin: 20% = 100
    if f.get("profit_margin") is not None:
        subs["margin"] = max(0.0, min(100.0, f["profit_margin"] * 500.0))
    # PEG: <1 great (=100), 2 = 0, missing = neutral skip
    peg = f.get("peg")
    if peg is not None and peg > 0:
        subs["peg"] = max(0.0, min(100.0, (2.0 - peg) * 100.0))
    # Debt/Equity (D/E is in % per yfinance, e.g., 180 = 1.8x): 0 = 100, 300 = 0
    de = f.get("debt_to_equity")
    if de is not None:
        subs["balance_sheet"] = max(0.0, min(100.0, 100.0 - de / 3.0))

    if not subs:
        return None, {}
    composite = round(sum(subs.values()) / len(subs), 1)
    return composite, {k: round(v, 1) for k, v in subs.items()}


def _fund_signals(subs: dict) -> list[str]:
    out = []
    if subs.get("rev_growth", 0) >= 80: out.append("strong-revenue-growth")
    if subs.get("eps_growth", 0) >= 80: out.append("strong-earnings-growth")
    if subs.get("margin", 0) >= 80: out.append("high-margin")
    if subs.get("peg", 0) >= 70: out.append("reasonable-valuation")
    if subs.get("balance_sheet", 0) >= 80: out.append("strong-balance-sheet")
    return out


def load_after_hours_data() -> dict[str, dict]:
    """Read data/after_hours.json if present. Returns dict symbol -> AH row."""
    if not AFTER_OUT.exists():
        return {}
    try:
        ah = json.loads(AFTER_OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for r in ah.get("surges", []) + ah.get("drops", []):
        out[r["symbol"]] = {
            "ah_pct": r.get("ah_pct"),
            "ext_price": r.get("ext_price"),
            "regular_close": r.get("regular_close"),
            "updated_at": ah.get("updated_at"),
        }
    return out


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
        if sym == BENCH or sym in SECTOR_ETFS or sym in THEME_ETFS:
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

    # Shared fundamentals cache (used by both Tomorrow's Setups and Long-Term picks).
    fund_cache: dict[str, dict | None] = {}
    def get_fund(sym: str):
        if sym not in fund_cache:
            fund_cache[sym] = fetch_fundamentals(sym)
            time.sleep(0.1)
        return fund_cache[sym]

    # Tomorrow's Setups: enrich the top-20 tech-composite candidates with
    # fundamentals and after-hours confirmation, then re-rank.
    ah_data = load_after_hours_data()
    setup_pool = top_picks_rows[:20]  # widen pool before re-ranking
    print(f"[daily] Fetching fundamentals for {len(setup_pool)} setup candidates…", flush=True)
    tomorrow_setups = []
    for r in setup_pool:
        sym = r["symbol"]
        fund = get_fund(sym)
        fund_score, fund_subs = score_fundamentals(fund) if fund else (None, {})
        ah = ah_data.get(sym)
        ah_pct = ah["ah_pct"] if ah else None

        # AH boost: +AH_pct mapped to 0-100 via tanh-ish clamp. 0 at AH=0, ~50 at +5%, ~100 at +15%.
        if ah_pct is None:
            ah_boost = 50.0  # neutral if no AH data
        else:
            ah_boost = max(0.0, min(100.0, 50.0 + ah_pct * 4.0))

        # Weighted setup score; if fundamentals missing, redistribute weight to tech.
        if fund_score is None:
            setup_score = W_TECH / (W_TECH + W_AH) * r["score"] + W_AH / (W_TECH + W_AH) * ah_boost
            blend = "tech+ah only (no fund data)"
        else:
            setup_score = W_TECH * r["score"] + W_FUND * fund_score + W_AH * ah_boost
            blend = "tech+fund+ah"
        setup_score = round(setup_score, 1)

        # Earnings risk
        warnings = []
        dte = fund.get("days_to_earnings") if fund else None
        if dte is not None and 0 <= dte <= EARNINGS_RISK_WINDOW_DAYS:
            warnings.append(f"earnings-in-{dte}d")
            setup_score = round(setup_score - 10, 1)  # binary-event penalty

        sigs = list(r["signals"])
        if fund:
            sigs.extend(_fund_signals(fund_subs))
        if ah_pct is not None and ah_pct >= 2:
            sigs.append("ah-surge")
        elif ah_pct is not None and ah_pct <= -2:
            sigs.append("ah-drop")

        tomorrow_setups.append({
            "symbol": sym,
            "price": r["price"],
            "day_pct": r["day_pct"],
            "month_pct": r["month_pct"],
            "rs_vs_spy": r["rs_vs_spy"],
            "tech_score": r["score"],
            "fund_score": fund_score,
            "ah_boost": round(ah_boost, 1),
            "setup_score": setup_score,
            "signals": sigs,
            "warnings": warnings,
            "blend": blend,
            "fundamentals": fund,
            "after_hours": ah,
        })
    tomorrow_setups.sort(key=lambda r: r["setup_score"], reverse=True)
    tomorrow_setups = tomorrow_setups[:SETUP_TOP_N]

    # Long-term ideas: quality businesses (fundamentals) that are also market
    # leaders (RS). Pull fundamentals for the top RS names, keep the growing &
    # profitable ones, rank 60% fundamentals / 40% relative strength.
    lt_pool = sorted([r for r in rows if r["three_month_pct"] is not None],
                     key=lambda r: r["three_month_pct"], reverse=True)[:LT_POOL_N]
    print(f"[daily] Fetching fundamentals for {len(lt_pool)} long-term candidates…", flush=True)
    elig_lt = []
    for r in lt_pool:
        f = get_fund(r["symbol"])
        fs, _ = score_fundamentals(f) if f else (None, {})
        if fs is None or f is None:
            continue
        if (f.get("rev_growth") or 0) <= 0:
            continue  # must be growing
        if (f.get("profit_margin") or 0) <= 0 and (f.get("eps_growth") or 0) <= 0:
            continue  # must be profitable or earnings-growing (no junk)
        elig_lt.append((r, f, fs))
    lt_rs_pct = percentile_rank([(rr["rs_vs_spy"] or 0.0) for (rr, _, _) in elig_lt])
    long_term_rows = []
    for i, (r, f, fs) in enumerate(elig_lt):
        long_term_rows.append({
            "symbol": r["symbol"],
            "price": r["price"],
            "lt_score": round(0.6 * fs + 0.4 * lt_rs_pct[i], 1),
            "fund_score": fs,
            "rev_growth": f.get("rev_growth"),
            "eps_growth": f.get("eps_growth"),
            "profit_margin": f.get("profit_margin"),
            "forward_pe": f.get("forward_pe"),
            "peg": f.get("peg"),
            "rs_vs_spy": r["rs_vs_spy"],
            "three_month_pct": r["three_month_pct"],
        })
    long_term_rows.sort(key=lambda r: r["lt_score"], reverse=True)
    long_term_picks = long_term_rows[:LONG_TERM_N]

    # Weekly sector leaders: ~5-trading-day return of each SPDR sector ETF.
    sector_rows = []
    for etf, sector_name in SECTOR_ETFS.items():
        df = data.get(etf)
        if df is None:
            continue
        c = df["Close"].dropna()
        if len(c) < 6:
            continue
        wk = pct_change(float(c.iloc[-1]), float(c.iloc[-6]))
        if wk is None:
            continue
        sector_rows.append({"sector": sector_name, "etf": etf, "week_pct": round(wk, 2)})
    sector_rows.sort(key=lambda r: r["week_pct"], reverse=True)
    sector_week = {"leader": sector_rows[0] if sector_rows else None, "ranked": sector_rows}

    # Finer industries & themes (semis, software, space/defense, uranium, etc.).
    industry_rows = []
    for etf, label in THEME_ETFS.items():
        df = data.get(etf)
        if df is None:
            continue
        c = df["Close"].dropna()
        if len(c) < 6:
            continue
        wk = pct_change(float(c.iloc[-1]), float(c.iloc[-6]))
        if wk is None:
            continue
        industry_rows.append({"sector": label, "etf": etf, "week_pct": round(wk, 2)})
    industry_rows.sort(key=lambda r: r["week_pct"], reverse=True)
    industry_week = {"leader": industry_rows[0] if industry_rows else None, "ranked": industry_rows}

    # Analyst Watchlist: curated 2026 long-term ideas joined with live metrics.
    rows_by_sym = {r["symbol"]: r for r in rows}
    analyst_watchlist = []
    for sym, info in ANALYST_WATCHLIST.items():
        r = rows_by_sym.get(sym)
        analyst_watchlist.append({
            "symbol": sym, "firms": info[0], "thesis": info[1],
            "price": r["price"] if r else None,
            "month_pct": r["month_pct"] if r else None,
            "rs_vs_spy": r["rs_vs_spy"] if r else None,
        })
    analyst_watchlist.sort(key=lambda x: (x["rs_vs_spy"] is None, -(x["rs_vs_spy"] or 0)))

    # Name lookups only for displayed tickers
    display_syms = set()
    for r in breakouts + vol_surges + top_rs + top_picks + tomorrow_setups + long_term_picks + analyst_watchlist:
        display_syms.add(r["symbol"])
    print(f"[daily] Looking up names for {len(display_syms)} displayed tickers…", flush=True)
    name_cache: dict[str, str] = {}
    for sym in display_syms:
        get_name_cached(sym, name_cache)
        time.sleep(0.05)
    for r in breakouts + vol_surges + top_rs + top_picks + tomorrow_setups + long_term_picks + analyst_watchlist:
        r["name"] = name_cache.get(r["symbol"], "")

    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "universe_size": len(data),
        "spy_3m_pct": round(spy_3m, 2) if spy_3m is not None else None,
        "thresholds": {
            "near_high_pct": NEAR_HIGH_PCT,
            "vol_surge_mult": VOL_SURGE_MULT,
            "breakout_min_month_pct": BREAKOUT_MIN_MONTH_PCT,
            "setup_blend_weights": {"tech": W_TECH, "fund": W_FUND, "ah": W_AH},
            "earnings_risk_window_days": EARNINGS_RISK_WINDOW_DAYS,
        },
        "tomorrow_setups": tomorrow_setups,
        "long_term_picks": long_term_picks,
        "analyst_watchlist": analyst_watchlist,
        "sector_week": sector_week,
        "industry_week": industry_week,
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
    for etf in list(SECTOR_ETFS) + list(THEME_ETFS):
        if etf not in syms:
            syms.append(etf)

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
            f"{len(result['tomorrow_setups'])} tomorrow setups, "
            f"{len(result['long_term_picks'])} long-term ideas, "
            f"sector leader {result['sector_week']['leader']['sector'] if result['sector_week']['leader'] else 'n/a'}, "
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
