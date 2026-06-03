"""Market-data aggregation.

Primary source: Yahoo Finance via the `yfinance` library — prices, fundamentals,
and Wall-Street analyst consensus (Yahoo aggregates ratings/price targets from
many brokerages). Optional fallback: Alpha Vantage for a live quote.

NOTE ON DATA SOURCES: brokerage platforms (Webull, IBKR, moomoo, Tiger,
Robinhood) require authenticated accounts, and Bloomberg / WSJ / Reuters /
Seeking Alpha are paywalled — none expose a free, aggregatable market-data API,
and scraping them violates their terms. So this module standardizes on Yahoo
Finance (free, broad coverage, includes analyst consensus). `_alpha_vantage_quote`
shows the adapter pattern for plugging in another provider; add more the same way.
"""
from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from . import ibkr, indicators

# Where price/history come from: "yahoo" (default), "ibkr", or "auto"
# ("auto" = IBKR when its gateway is authenticated, otherwise Yahoo).
DATA_SOURCE = os.environ.get("DATA_SOURCE", "yahoo").lower()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _clean(value):
    """Make a single value JSON-safe (NaN/inf -> None)."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _series_to_lists(df: pd.DataFrame) -> dict:
    """Convert a DataFrame to {column: [json-safe values]} with NaN -> None."""
    out: dict[str, list] = {}
    for col in df.columns:
        out[col] = [_clean(v) for v in df[col].tolist()]
    return out


def _round(value, digits=2):
    return None if value is None else round(float(value), digits)


# --------------------------------------------------------------------------- #
# Yahoo Finance access
# --------------------------------------------------------------------------- #
def get_history(ticker: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    """Daily OHLCV history. 2y is enough to seed a valid 200-day SMA."""
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    if df is None or df.empty:
        raise ValueError(f"No price history found for '{ticker}'. Check the symbol.")
    return df.dropna(subset=["Close"])


def _indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"]
    macd_line, signal_line, hist = indicators.macd(close)
    return pd.DataFrame(
        {
            "date": df.index.strftime("%Y-%m-%d"),
            "close": close.round(4),
            "sma50": indicators.sma(close, 50).round(4),
            "sma200": indicators.sma(close, 200).round(4),
            "rsi": indicators.rsi(close, 14).round(2),
            "macd": macd_line.round(4),
            "macd_signal": signal_line.round(4),
            "macd_hist": hist.round(4),
            "volume": df["Volume"].fillna(0).astype("int64"),
        }
    )


def _derive_latest(frame: pd.DataFrame) -> dict:
    """Latest indicator readings plus a few interpreted flags for the model/UI."""
    last = frame.iloc[-1]

    def g(key):
        return _clean(None if pd.isna(last[key]) else float(last[key]))

    close = g("close")
    sma50 = g("sma50")
    sma200 = g("sma200")
    rsi = g("rsi")
    macd_v = g("macd")
    macd_sig = g("macd_signal")

    def pct_vs(level):
        if close is None or not level:
            return None
        return round((close - level) / level * 100, 2)

    trend = None
    if sma50 is not None and sma200 is not None:
        trend = "golden-cross (50d above 200d)" if sma50 > sma200 else "death-cross (50d below 200d)"

    rsi_zone = None
    if rsi is not None:
        rsi_zone = "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "neutral"

    macd_state = None
    if macd_v is not None and macd_sig is not None:
        macd_state = "bullish (MACD above signal)" if macd_v > macd_sig else "bearish (MACD below signal)"

    return {
        "close": close,
        "sma50": sma50,
        "sma200": sma200,
        "rsi": rsi,
        "macd": macd_v,
        "macd_signal": macd_sig,
        "macd_hist": g("macd_hist"),
        "price_vs_sma50_pct": pct_vs(sma50),
        "price_vs_sma200_pct": pct_vs(sma200),
        "trend": trend,
        "rsi_zone": rsi_zone,
        "macd_state": macd_state,
    }


def _get_info(ticker: str) -> dict:
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


def _classify_sentiment(title: str) -> str:
    """Very light keyword sentiment for headline coloring (POS/NEG/NEU)."""
    t = (title or "").lower()
    pos = ("beat", "surge", "record", "soar", "jump", "rally", "upgrade", "raises",
           "raise", "growth", "strong", "wins", "approval", "partnership", "buyback")
    neg = ("miss", "fall", "drop", "plunge", "slump", "downgrade", "cut", "lawsuit",
           "probe", "investigation", "recall", "warning", "weak", "loss", "decline")
    if any(w in t for w in pos):
        return "POS"
    if any(w in t for w in neg):
        return "NEG"
    return "NEU"


def _get_news(ticker: str, limit: int = 8) -> list[dict]:
    """Recent headlines from Yahoo Finance (best-effort; empty list on failure)."""
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []
    items: list[dict] = []
    for n in raw[:limit]:
        # yfinance returns either a flat dict or a nested {"content": {...}} shape.
        content = n.get("content") if isinstance(n.get("content"), dict) else n
        title = content.get("title") or n.get("title")
        if not title:
            continue
        # publisher
        prov = content.get("provider") or {}
        publisher = (
            prov.get("displayName")
            if isinstance(prov, dict)
            else n.get("publisher")
        ) or n.get("publisher")
        # link
        link = None
        cu = content.get("canonicalUrl") or content.get("clickThroughUrl")
        if isinstance(cu, dict):
            link = cu.get("url")
        link = link or n.get("link")
        # timestamp
        ts = n.get("providerPublishTime") or content.get("pubDate")
        when = None
        if isinstance(ts, (int, float)):
            when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        elif isinstance(ts, str):
            when = ts
        summary = content.get("summary") or content.get("description")
        items.append(
            {
                "title": title,
                "publisher": publisher,
                "link": link,
                "published": when,
                "summary": summary,
                "sentiment": _classify_sentiment(title),
            }
        )
    return items


def _peer_snapshot(ticker: str) -> dict | None:
    """A compact, JSON-safe row for a single peer (price, change, cap, P/E, margin)."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
    chg = None
    if price and prev:
        chg = round((price - prev) / prev * 100, 2)
    return {
        "ticker": ticker.upper(),
        "name": info.get("shortName") or info.get("longName") or ticker.upper(),
        "price": _round(price, 2),
        "change_pct": chg,
        "market_cap": info.get("marketCap"),
        "trailing_pe": _round(info.get("trailingPE")),
        "profit_margin": _round(info.get("profitMargins"), 4),
        "revenue": info.get("totalRevenue"),
    }


# Browser-like headers; Yahoo's JSON endpoints reject requests without them.
_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _yf_json(url: str) -> dict:
    """GET a Yahoo Finance JSON endpoint (stdlib only). Returns {} on any error."""
    try:
        req = urllib.request.Request(url, headers=_YF_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8")) or {}
    except Exception:
        return {}


def _parse_recommended_symbols(data: dict) -> list[str]:
    """Pull the peer symbols out of a recommendationsbysymbol JSON payload."""
    out: list[str] = []
    results = (
        (data.get("finance", {}) or {}).get("result")
        or data.get("recommendedSymbols")
        or []
    )
    for entry in results:
        for item in (entry or {}).get("recommendedSymbols", []) or []:
            sym = (item or {}).get("symbol")
            if sym:
                out.append(sym.upper())
    return out


def _peers_recommendations_by_symbol(ticker: str) -> list[str]:
    """DYNAMIC peer discovery: ask Yahoo for the symbols related to *this* ticker.

    Yahoo's ``recommendationsbysymbol`` endpoint returns the "people also follow"
    list for whatever symbol is passed — i.e. peers are looked up live from the
    searched ticker, with no hardcoded mapping.

    The request is routed through yfinance's ``YfData`` session, which uses
    ``curl_cffi`` browser impersonation plus Yahoo's cookie/crumb handshake. That
    matters in production: Yahoo blocks bare ``urllib`` calls from cloud/data-center
    IPs (e.g. Render), but accepts the impersonated session. Plain ``urllib`` is
    kept only as a last-ditch local fallback."""
    quoted = urllib.parse.quote(ticker)

    # Primary: yfinance's authenticated, browser-impersonating session.
    try:
        import yfinance.data as _ydata  # lazy import

        yf_data = _ydata.YfData()
        for host in ("query2", "query1"):
            url = (
                f"https://{host}.finance.yahoo.com/v6/finance/"
                f"recommendationsbysymbol/{quoted}"
            )
            try:
                data = yf_data.get_raw_json(url) or {}
            except Exception:
                data = {}
            syms = _parse_recommended_symbols(data)
            if syms:
                return syms
    except Exception:
        pass

    # Fallback: plain urllib (works locally / residential IPs).
    for host in ("query2", "query1"):
        url = (
            f"https://{host}.finance.yahoo.com/v6/finance/"
            f"recommendationsbysymbol/{quoted}"
        )
        syms = _parse_recommended_symbols(_yf_json(url))
        if syms:
            return syms
    return []


def _peers_from_yf_recommendations(ticker: str) -> list[str]:
    """Fallback: peer symbols from the yfinance ``.recommendations`` attribute."""
    out: list[str] = []
    try:
        rec = getattr(yf.Ticker(ticker), "recommendations", None)
        if isinstance(rec, list):
            for r in rec:
                sym = (r or {}).get("symbol")
                if sym:
                    out.append(sym.upper())
    except Exception:
        pass
    return out


def _peers_by_sector_screen(base_info: dict, limit: int = 12) -> list[str]:
    """Fallback: find tickers that share the searched symbol's sector/industry via
    Yahoo's screener. Still driven by the searched symbol — its own sector/industry
    is the filter, so no peer list is hardcoded."""
    sector = (base_info or {}).get("sector")
    industry = (base_info or {}).get("industry") or (base_info or {}).get("industryKey")
    if not sector and not industry:
        return []

    # Yahoo's predefined screener requires its own sector keys; the free-form
    # query screener lets us filter directly on the searched symbol's sector.
    body = {
        "size": limit,
        "offset": 0,
        "sortField": "intradaymarketcap",
        "sortType": "DESC",
        "quoteType": "EQUITY",
        "query": {
            "operator": "AND",
            "operands": [
                {"operator": "EQ", "operands": ["sector", sector]} if sector else None,
                {"operator": "EQ", "operands": ["region", "us"]},
            ],
        },
    }
    body["query"]["operands"] = [op for op in body["query"]["operands"] if op]

    out: list[str] = []
    for host in ("query2", "query1"):
        url = (
            f"https://{host}.finance.yahoo.com/v1/finance/screener?"
            "crumb=&lang=en-US&region=US"
        )
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={**_YF_HEADERS, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8")) or {}
        except Exception:
            data = {}
        results = (data.get("finance", {}) or {}).get("result") or []
        for r in results:
            for q in (r or {}).get("quotes", []) or []:
                sym = (q or {}).get("symbol")
                if sym:
                    out.append(sym.upper())
        if out:
            break
    return out


def _get_peers(ticker: str, base_info: dict, limit: int = 6) -> list[dict]:
    """Sector peers for the comparison table — discovered DYNAMICALLY from the
    searched symbol (no hardcoded peer lists):

      1. Yahoo ``recommendationsbysymbol`` ("people also follow" for this ticker).
      2. yfinance ``.recommendations`` attribute (same idea, different transport).
      3. Yahoo screener filtered by the searched symbol's own sector/industry.

    The queried ticker is always the first row. Capped to ``limit`` to respect
    rate limits."""
    tu = ticker.upper()

    candidates: list[str] = _peers_recommendations_by_symbol(ticker)
    if not candidates:
        candidates = _peers_from_yf_recommendations(ticker)
    if not candidates:
        candidates = _peers_by_sector_screen(base_info)

    # De-dupe, drop self, cap the count to limit API calls.
    seen, peers_syms = {tu}, []
    for s in candidates:
        su = (s or "").upper()
        if su and su not in seen:
            seen.add(su)
            peers_syms.append(su)
        if len(peers_syms) >= limit:
            break

    rows: list[dict] = []
    self_row = _peer_snapshot(ticker)
    if self_row:
        rows.append(self_row)
    for s in peers_syms:
        row = _peer_snapshot(s)
        if row:
            rows.append(row)
    return rows


def _alpha_vantage_quote(ticker: str) -> float | None:
    """Optional fallback live quote via Alpha Vantage (stdlib only)."""
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        return None
    try:
        qs = urllib.parse.urlencode(
            {"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": key}
        )
        with urllib.request.urlopen(
            f"https://www.alphavantage.co/query?{qs}", timeout=10
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        price = data.get("Global Quote", {}).get("05. price")
        return float(price) if price else None
    except Exception:
        return None


def get_market_snapshot(ticker: str, lookback: int = 250) -> dict:
    """Assemble a single JSON-safe snapshot: meta, quote, indicators, fundamentals,
    analyst consensus, and the chart series (last `lookback` trading days)."""
    ticker = ticker.strip().upper()

    # --- price history: IBKR (optional) or Yahoo (default/fallback) ---
    used_ibkr = False
    df = None
    if DATA_SOURCE in ("ibkr", "auto") and ibkr.available():
        try:
            df = ibkr.get_history(ticker)
            used_ibkr = True
        except Exception:
            if DATA_SOURCE == "ibkr":
                raise
            df = None  # auto: fall through to Yahoo
    if df is None:
        df = get_history(ticker)  # Yahoo Finance

    frame = _indicator_frame(df)
    latest = _derive_latest(frame)
    info = _get_info(ticker)  # fundamentals + analyst consensus (always Yahoo)

    # --- live price: IBKR snapshot first when in use, then Yahoo, then fallbacks ---
    price = ibkr.snapshot_price(ticker) if used_ibkr else None
    if price is None:
        price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price is None:
        price = _alpha_vantage_quote(ticker)
    if price is None:  # last resort: most recent close
        price = latest["close"]
    prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
    day_change_pct = None
    if price and prev_close:
        day_change_pct = round((price - prev_close) / prev_close * 100, 2)

    meta = {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("currency", "USD"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
    }
    quote = {
        "price": _round(price, 2),
        "previous_close": _round(prev_close, 2),
        "day_change_pct": day_change_pct,
        "fifty_two_week_high": _round(info.get("fiftyTwoWeekHigh"), 2),
        "fifty_two_week_low": _round(info.get("fiftyTwoWeekLow"), 2),
    }
    fundamentals = {
        "market_cap": info.get("marketCap"),
        "trailing_pe": _round(info.get("trailingPE")),
        "forward_pe": _round(info.get("forwardPE")),
        "peg_ratio": _round(info.get("pegRatio")),
        "profit_margin": _round(info.get("profitMargins"), 4),
        "revenue_growth": _round(info.get("revenueGrowth"), 4),
        "earnings_growth": _round(info.get("earningsGrowth"), 4),
        "beta": _round(info.get("beta")),
        "dividend_yield": _round(info.get("dividendYield"), 4),
    }
    analyst = {
        "recommendation": info.get("recommendationKey"),
        "recommendation_mean": _round(info.get("recommendationMean")),
        "num_analysts": info.get("numberOfAnalystOpinions"),
        "target_mean": _round(info.get("targetMeanPrice"), 2),
        "target_high": _round(info.get("targetHighPrice"), 2),
        "target_low": _round(info.get("targetLowPrice"), 2),
    }
    if analyst["target_mean"] and quote["price"]:
        analyst["target_upside_pct"] = round(
            (analyst["target_mean"] - quote["price"]) / quote["price"] * 100, 2
        )
    else:
        analyst["target_upside_pct"] = None

    series = _series_to_lists(frame.tail(lookback))

    # Optional extras (best-effort; never fail the whole request on these).
    try:
        news = _get_news(ticker)
    except Exception:
        news = []
    try:
        peers = _get_peers(ticker, info)
    except Exception:
        peers = []

    return {
        "meta": meta,
        "quote": quote,
        "indicators": latest,
        "fundamentals": fundamentals,
        "analyst": analyst,
        "series": series,
        "news": news,
        "peers": peers,
        "data_source": (
            "IBKR Client Portal — prices/history; Yahoo Finance — fundamentals & analyst consensus"
            if used_ibkr
            else "Yahoo Finance (prices, fundamentals, analyst consensus)"
        ),
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
