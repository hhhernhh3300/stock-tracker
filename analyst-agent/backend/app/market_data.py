"""Market-data aggregation — multi-source, resilient on shared cloud IPs.

Primary:  Yahoo Finance via yfinance (prices, fundamentals, analyst consensus).
          Three-tier fallback so metrics still populate when Yahoo rate-limits:
            1. yf.Ticker().info          — richest, most throttled on Render
            2. /v10/finance/quoteSummary — same richness, different code-path
            3. /v7/finance/quote batch   — lighter but covers price/cap/PE/sector

Optional: set any of these env vars for additional data enrichment:
  ALPHAVANTAGE_API_KEY  — Alpha Vantage OVERVIEW (fundamentals, analyst targets)
                          free tier: 25 req/day  https://www.alphavantage.co
  FMP_API_KEY           — Financial Modeling Prep profile + key metrics
                          free tier: 250 req/day  https://financialmodelingprep.com

NOTE: brokerage APIs (Webull, IBKR, moomoo), Bloomberg, Reuters, Seeking Alpha
are either auth-gated or paywalled and cannot be used without user credentials.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from . import asset_class, ibkr, indicators

# Thread pool for hard wall-clock timeouts on slow network calls. yfinance's
# internal HTTP calls have their own long timeouts we can't always cap, so we
# run the network-heavy steps in a worker and abandon them if they overrun —
# guaranteeing the API responds well within Render's gateway limit (no 502s).
# Sized generously so abandoned (orphaned) calls that are still draining can't
# starve fresh requests of a worker during a throttle window.
_NET_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=32)


def _run_budget(fn, timeout, default, *args, **kwargs):
    """Run fn(*args) with a hard wall-clock `timeout` (seconds).

    Returns the result if it finishes in time, otherwise `default`. An overrunning
    call keeps running in its worker thread but its result is discarded — the API
    request returns immediately instead of hanging on a throttled Yahoo endpoint."""
    try:
        return _NET_POOL.submit(fn, *args, **kwargs).result(timeout=timeout)
    except Exception:
        return default

# Where price/history come from: "yahoo" (default), "ibkr", or "auto"
# ("auto" = IBKR when its gateway is authenticated, otherwise Yahoo).
DATA_SOURCE = os.environ.get("DATA_SOURCE", "yahoo").lower()

# Optional Cloudflare-Worker proxy for Yahoo. Yahoo blocks shared cloud IPs
# (Render) for its authenticated data endpoints; routing those calls through a
# Worker on Cloudflare's (non-blocked) edge IPs makes fundamentals reliable.
# Set YAHOO_PROXY_BASE to the Worker URL (e.g. https://yahoo-proxy.xxx.workers.dev).
# When set, the Worker handles Yahoo's cookie/crumb handshake itself, so the
# backend just forwards the path.
_YAHOO_PROXY = os.environ.get("YAHOO_PROXY_BASE", "").strip().rstrip("/")
_YAHOO_PROXY_TOKEN = os.environ.get("YAHOO_PROXY_TOKEN", "").strip()
_YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def _proxify(url: str) -> str:
    """Rewrite a Yahoo data URL to go through the configured Worker proxy.

    No-op when no proxy is set or the URL isn't a Yahoo data host. Idempotent."""
    if not _YAHOO_PROXY:
        return url
    for h in _YAHOO_HOSTS:
        prefix = f"https://{h}"
        if url.startswith(prefix):
            out = _YAHOO_PROXY + url[len(prefix):]
            if _YAHOO_PROXY_TOKEN:
                out += ("&" if "?" in out else "?") + "token=" + urllib.parse.quote(_YAHOO_PROXY_TOKEN)
            return out
    return url


def proxy_configured() -> bool:
    return bool(_YAHOO_PROXY)


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


def _num(value):
    """Coerce a Yahoo value to a float, or None.

    Handles plain numbers, numeric strings, AND Yahoo's {"raw": n, "fmt": "…"}
    wrapper dicts (which appear for some ETF/crypto fields and would otherwise
    crash float()). Anything non-numeric → None."""
    if isinstance(value, dict):
        value = value.get("raw")
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _round(value, digits=2):
    n = _num(value)
    return None if n is None else round(n, digits)


# --------------------------------------------------------------------------- #
# Yahoo Finance access
# --------------------------------------------------------------------------- #
def _chart_history(ticker: str, rng: str = "2y", interval: str = "1d"):
    """Build an OHLCV DataFrame straight from the /v8/finance/chart endpoint.

    This goes through ``_yf_get_json`` — so it is routed via the Cloudflare proxy
    when one is configured, letting price history work even when Yahoo blocks
    Render's IP for the direct yfinance call. Returns a DataFrame or None."""
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(ticker)}?range={rng}&interval={interval}"
    )
    data = _yf_get_json(url)
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return None
    ts = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    closes = q.get("close") or []
    if not ts or not closes:
        return None
    n = len(ts)

    def col(key):
        v = q.get(key) or []
        return list(v) + [None] * (n - len(v)) if len(v) < n else v[:n]

    df = pd.DataFrame(
        {
            "Open": col("open"),
            "High": col("high"),
            "Low": col("low"),
            "Close": col("close"),
            "Volume": col("volume"),
        },
        index=pd.to_datetime(ts, unit="s"),
    )
    return df.dropna(subset=["Close"])


def get_history(ticker: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    """Daily OHLCV history. 2y is enough to seed a valid 200-day SMA.

    When a Cloudflare proxy is configured we fetch the chart endpoint THROUGH it
    (Render's IP is blocked for yfinance's direct calls). Otherwise we use
    yfinance directly and fall back to the chart endpoint if that returns empty
    or is rate-limited."""
    # Proxy configured → go straight through it (non-blocked IP).
    if _YAHOO_PROXY:
        df = _chart_history(ticker, period, interval)
        if df is not None and not df.empty:
            return df

    # Direct yfinance (with an explicit timeout so a throttled endpoint can't
    # hang a pool worker as an orphan thread).
    try:
        df = yf.Ticker(ticker).history(
            period=period, interval=interval, auto_adjust=False, timeout=10
        )
    except Exception:
        df = None
    if df is not None and not df.empty:
        return df.dropna(subset=["Close"])

    # Last resort: the raw chart endpoint (also proxied when configured).
    df = _chart_history(ticker, period, interval)
    if df is not None and not df.empty:
        return df

    raise ValueError(f"No price history found for '{ticker}'. Check the symbol.")


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


def _info_is_rich(info: dict) -> bool:
    """Return True when `info` carries meaningful fundamental data."""
    return bool(
        info.get("sector")
        and (info.get("trailingPE") or info.get("marketCap"))
        and info.get("profitMargins") is not None
    )


def _merge_missing(dst: dict, src: dict, keys: tuple) -> None:
    """Copy src[key] → dst[key] only when dst[key] is absent/falsy."""
    for k in keys:
        if not dst.get(k) and src.get(k) is not None:
            dst[k] = src[k]


_COMMON_KEYS = (
    "sector", "industry", "shortName", "longName",
    "currentPrice", "regularMarketPrice", "regularMarketPreviousClose",
    "regularMarketChangePercent", "previousClose",
    "marketCap", "trailingPE", "forwardPE", "pegRatio", "trailingPegRatio",
    "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "currency", "fullExchangeName", "exchange",
    "profitMargins", "revenueGrowth", "earningsGrowth",
    "dividendYield", "trailingAnnualDividendYield",
    "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "numberOfAnalystOpinions", "recommendationKey", "recommendationMean",
)


# ─────────────────────────────────────────────────────────────────────────── #
# Fundamentals cache — keeps metrics populated across throttle windows.        #
# Once a ticker's fundamentals are fetched successfully they're cached for 6h,  #
# so a later request that gets throttled still shows real numbers instead of    #
# blank "—" dashes. Fundamentals barely change intraday, so this is safe.      #
# ─────────────────────────────────────────────────────────────────────────── #
_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_INFO_TTL = 21600.0  # 6 hours

# Fields worth caching/backfilling (the ones the UI shows as metrics).
_CACHEABLE_KEYS = _COMMON_KEYS + ("priceToBook",)


def _info_cache_get(ticker: str) -> dict:
    entry = _INFO_CACHE.get(ticker.upper())
    if entry and (time.time() - entry[0]) < _INFO_TTL:
        return entry[1]
    return {}


def _info_cache_put(ticker: str, info: dict) -> None:
    """Cache the non-empty fundamental fields of a successful fetch (merged with
    any prior cache, so a partial fetch never erases previously-known values)."""
    keep = {k: info[k] for k in _CACHEABLE_KEYS if info.get(k) is not None}
    if not keep:
        return
    prior = _INFO_CACHE.get(ticker.upper())
    base = dict(prior[1]) if prior else {}
    base.update(keep)
    _INFO_CACHE[ticker.upper()] = (time.time(), base)


def _get_info(ticker: str) -> dict:
    """Fundamentals + analyst consensus — three-tier Yahoo fallback chain.

    Tier 1  yf.Ticker().info
            Richest payload; internally calls quoteSummary but cached by
            yfinance.  Most aggressively rate-limited on shared cloud IPs.

    Tier 2  /v10/finance/quoteSummary
            Direct call with the same modules yfinance uses internally.
            Different URL path → often succeeds when .info is throttled.
            Covers all fundamentals, analyst targets, and sector/industry.

    Tier 3  /v7/finance/quote (batch)
            Lighter; covers price, market cap, P/E, beta, 52w range, sector.
            Does NOT carry profit margins / growth rates / analyst consensus,
            but keeps the basic fact sheet populated if both tiers 1 & 2 fail.

    Optional enrichment layers (applied after Yahoo tiers):
      Alpha Vantage OVERVIEW  when ALPHAVANTAGE_API_KEY is set
      FMP profile + metrics   when FMP_API_KEY is set
    """
    info: dict = {}

    # ── Tier 1: yfinance .info ───────────────────────────────────────────────
    # Skip when the auth breaker is open (IP throttled): .info hangs the longest
    # under throttle and would only fail slowly. Currency/price still come from
    # the chart endpoint, so we degrade fast instead of timing out the request.
    if not _auth_breaker_open():
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:
            info = {}

    # ── Tier 2: direct quoteSummary (if .info is thin) ──────────────────────
    if not _info_is_rich(info):
        try:
            qs = _yf_quote_summary(ticker)
            if qs:
                _merge_missing(info, qs, _COMMON_KEYS)
        except Exception:
            pass

    # ── Tier 3: batch /v7 quote (fill remaining price/cap/sector gaps) ───────
    if not info.get("sector") or not (
        info.get("currentPrice") or info.get("regularMarketPrice")
    ):
        try:
            q = _yf_quote_batch([ticker]).get(ticker.upper()) or {}
            _merge_missing(info, q, _COMMON_KEYS + (
                "trailingAnnualDividendYield", "priceToBook",
            ))
        except Exception:
            pass

    # dividendYield alias: some endpoints use trailingAnnualDividendYield
    if not info.get("dividendYield") and info.get("trailingAnnualDividendYield"):
        info["dividendYield"] = info["trailingAnnualDividendYield"]
    # pegRatio alias: Yahoo frequently returns trailingPegRatio instead
    if not info.get("pegRatio") and info.get("trailingPegRatio"):
        info["pegRatio"] = info["trailingPegRatio"]

    # ── Optional: Alpha Vantage OVERVIEW ────────────────────────────────────
    if not _info_is_rich(info):
        try:
            av = _alpha_vantage_overview(ticker)
            if av:
                _merge_missing(info, av, _COMMON_KEYS)
        except Exception:
            pass

    # ── Optional: Financial Modeling Prep ───────────────────────────────────
    if not _info_is_rich(info):
        try:
            fmp = _fmp_fundamentals(ticker)
            if fmp:
                _merge_missing(info, fmp, _COMMON_KEYS)
        except Exception:
            pass

    # ── Cache layer: persist good data, backfill gaps from a recent fetch ────
    # If this fetch came back rich, refresh the cache. Then backfill ANY field
    # still missing from the last good fetch — so a throttled response shows real
    # numbers (from cache) instead of "—" dashes.
    if _info_is_rich(info):
        _info_cache_put(ticker, info)
    else:
        cached = _info_cache_get(ticker)
        if cached:
            _merge_missing(info, cached, _CACHEABLE_KEYS)
            # Even a partial live fetch is worth caching its fresh fields.
            _info_cache_put(ticker, info)

    return info


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
    price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    prev = _num(info.get("regularMarketPreviousClose")) or _num(info.get("previousClose"))
    chg = None
    if price and prev:
        chg = round((price - prev) / prev * 100, 2)
    return {
        "ticker": ticker.upper(),
        "name": info.get("shortName") or info.get("longName") or ticker.upper(),
        "price": _round(price, 2),
        "change_pct": chg,
        "market_cap": _num(info.get("marketCap")),
        "trailing_pe": _round(info.get("trailingPE")),
        "profit_margin": _round(info.get("profitMargins"), 4),
        "revenue": _num(info.get("totalRevenue")),
        "currency": info.get("currency"),
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
    """GET a Yahoo Finance JSON endpoint (stdlib only). Returns {} on any error.
    Routes through the Cloudflare proxy when configured."""
    try:
        req = urllib.request.Request(_proxify(url), headers=_YF_HEADERS)
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


def _yf_data_session():
    """Return a yfinance ``YfData`` session (curl_cffi + cookie/crumb), or None.

    This session impersonates a real browser and completes Yahoo's crumb
    handshake, so it succeeds from cloud/data-center IPs (e.g. Render) where bare
    ``urllib`` is blocked. Cached on the function for reuse."""
    cached = getattr(_yf_data_session, "_cache", None)
    if cached is not None:
        return cached
    try:
        import yfinance.data as _ydata  # lazy import

        cached = _ydata.YfData()
    except Exception:
        cached = None
    _yf_data_session._cache = cached  # type: ignore[attr-defined]
    return cached


def _yf_get_json(url: str) -> dict:
    """GET a Yahoo JSON endpoint, preferring the yfinance session, urllib as last
    resort. Returns {} on any failure. Routes through the Cloudflare proxy when
    configured (the proxy completes Yahoo's crumb handshake on a non-blocked IP)."""
    target = _proxify(url)
    session = _yf_data_session()
    if session is not None:
        try:
            data = session.get_raw_json(target)
            if data:
                return data
        except Exception:
            pass
    return _yf_json(url)  # _yf_json re-applies _proxify; idempotent


# ─────────────────────────────────────────────────────────────────────────── #
# Cookie + crumb authenticated fetch                                          #
# Yahoo's data endpoints (/v7/quote, /v10/quoteSummary) return 401 without a   #
# cookie + crumb. yfinance's .info handles this internally, but our DIRECT     #
# fallback calls did not — so non-US tickers (whose .info often gets throttled #
# on shared cloud IPs) came back empty. This completes the handshake ourselves #
# so the fallback tiers actually return data for global symbols.              #
# ─────────────────────────────────────────────────────────────────────────── #
# `retry_after` negative-caches a failed handshake so we don't repeat the slow
# fc.yahoo.com + getcrumb round-trip on every call (and every peer) while Yahoo
# is throttling this IP — that was turning one throttled request into 18-30s.
_CRUMB_STATE: dict = {"session": None, "crumb": None, "retry_after": 0.0}

# Circuit breaker: after several consecutive auth-fetch failures we assume the
# IP is throttled and short-circuit the expensive crumb path for a cooldown,
# so requests fail FAST (price + currency still come from the chart endpoint).
_AUTH_BREAKER: dict = {"until": 0.0, "fails": 0}
_AUTH_FAIL_THRESHOLD = 3
_AUTH_COOLDOWN = 120.0  # seconds


def _auth_breaker_open() -> bool:
    return time.time() < _AUTH_BREAKER["until"]


def _auth_record(success: bool) -> None:
    if success:
        _AUTH_BREAKER["fails"] = 0
        _AUTH_BREAKER["until"] = 0.0
    else:
        _AUTH_BREAKER["fails"] += 1
        if _AUTH_BREAKER["fails"] >= _AUTH_FAIL_THRESHOLD:
            _AUTH_BREAKER["until"] = time.time() + _AUTH_COOLDOWN


def _trip_breaker() -> None:
    """Force the breaker open — used when a budgeted fetch overruns its deadline,
    so subsequent requests skip the slow auth path during the cooldown."""
    _AUTH_BREAKER["fails"] = _AUTH_FAIL_THRESHOLD
    _AUTH_BREAKER["until"] = time.time() + _AUTH_COOLDOWN


def _yf_crumb_session():
    """Return (requests.Session, crumb) authenticated against Yahoo, or (None, None).

    Cached after a successful handshake; NEGATIVE-cached for 90s after a failure
    so we don't repeat the slow handshake on every call during a throttle window.
    Short timeouts keep a throttled handshake from hanging the request."""
    if _CRUMB_STATE["session"] is not None and _CRUMB_STATE["crumb"]:
        return _CRUMB_STATE["session"], _CRUMB_STATE["crumb"]
    if time.time() < _CRUMB_STATE["retry_after"]:
        return None, None  # negative-cached: skip the handshake for now
    try:
        import requests  # in requirements.txt

        s = requests.Session()
        s.headers.update(_YF_HEADERS)
        # 1. obtain consent cookies (short timeout — fail fast under throttle)
        for cookie_url in ("https://fc.yahoo.com", "https://finance.yahoo.com"):
            try:
                s.get(cookie_url, timeout=5)
                break
            except Exception:
                continue
        # 2. obtain the crumb tied to those cookies
        for host in ("query2", "query1"):
            try:
                r = s.get(
                    f"https://{host}.finance.yahoo.com/v1/test/getcrumb", timeout=5
                )
                crumb = (r.text or "").strip()
                # A valid crumb is short and not an HTML error page.
                if crumb and "<" not in crumb and len(crumb) < 40:
                    _CRUMB_STATE["session"] = s
                    _CRUMB_STATE["crumb"] = crumb
                    _CRUMB_STATE["retry_after"] = 0.0
                    return s, crumb
            except Exception:
                continue
    except Exception:
        pass
    # Handshake failed — don't retry it for 90s.
    _CRUMB_STATE["retry_after"] = time.time() + 90.0
    return None, None


def _reset_crumb() -> None:
    _CRUMB_STATE["session"] = None
    _CRUMB_STATE["crumb"] = None


def _yf_session_crumb():
    """Best-effort crumb from the yfinance YfData (curl_cffi) session.

    yfinance already completes Yahoo's cookie/crumb handshake using browser
    impersonation that works from Render's shared IP. Reusing its crumb — and
    fetching through its session — is the most Render-robust path. Returns
    (session, crumb) or (None, None)."""
    session = _yf_data_session()
    if session is None:
        return None, None
    crumb = None
    try:
        if hasattr(session, "_get_crumb"):
            crumb = session._get_crumb()
    except Exception:
        crumb = None
    if not crumb:
        crumb = getattr(session, "_crumb", None) or getattr(session, "crumb", None)
    return (session, crumb) if crumb else (None, None)


def _yf_payload_ok(data: dict) -> bool:
    """True only when `data` carries real results (not an error/empty payload)."""
    if not isinstance(data, dict):
        return False
    for top, key in (("quoteSummary", "result"), ("quoteResponse", "result"),
                     ("finance", "result")):
        node = data.get(top)
        if isinstance(node, dict):
            if node.get("error"):
                return False
            if node.get(key):
                return True
    return bool(data.get("quotes"))  # /v1/search shape


def _yf_auth_json(url: str) -> dict:
    """GET a Yahoo JSON endpoint WITH cookie + crumb auth (for /quote, /quoteSummary).

    Strategy, in order of Render-robustness:
      1. yfinance curl_cffi session + its crumb  (best — proven to work on Render)
      2. plain requests session + own handshake  (independent fallback path)
      3. bare yfinance session, no crumb          (last resort)
    Each strategy only short-circuits when it returns REAL results. A circuit
    breaker skips strategies 1-2 entirely after repeated failures (IP throttled)
    so requests fail FAST instead of hanging on every fallback timeout."""
    sep = "&" if "?" in url else "?"

    # Proxy configured → the Cloudflare Worker does the cookie/crumb handshake on
    # a non-blocked IP. Call it via _yf_get_json (which proxifies the URL).
    if _YAHOO_PROXY:
        data = _yf_get_json(url)
        if _yf_payload_ok(data):
            return data
        # else fall through to the direct paths below

    # Breaker open → IP looks throttled. Skip the slow auth path; the bare
    # endpoint still serves cached/price data and returns quickly.
    if _auth_breaker_open():
        return _yf_get_json(url)

    # ── Strategy 1: yfinance session + its own crumb ───────────────────────
    ysess, ycrumb = _yf_session_crumb()
    if ysess is not None and ycrumb:
        try:
            data = ysess.get_raw_json(f"{url}{sep}crumb={urllib.parse.quote(ycrumb)}")
            if _yf_payload_ok(data):
                _auth_record(True)
                return data
        except Exception:
            pass

    # ── Strategy 2: requests session + our own crumb handshake ─────────────
    session, crumb = _yf_crumb_session()
    if session is not None and crumb:
        full = f"{url}{sep}crumb={urllib.parse.quote(crumb)}"
        try:
            r = session.get(full, timeout=7)
            if r.status_code == 200:
                data = r.json() or {}
                if _yf_payload_ok(data):
                    _auth_record(True)
                    return data
            if r.status_code in (401, 403):
                _reset_crumb()
                session, crumb = _yf_crumb_session()
                if session is not None and crumb:
                    r = session.get(f"{url}{sep}crumb={urllib.parse.quote(crumb)}", timeout=7)
                    if r.status_code == 200:
                        data = r.json() or {}
                        if _yf_payload_ok(data):
                            _auth_record(True)
                            return data
        except Exception:
            pass

    # Both auth strategies failed → count toward the breaker.
    _auth_record(False)

    # ── Strategy 3: bare session, no crumb (may still be cached) ───────────
    return _yf_get_json(url)


# ─────────────────────────────────────────────────────────────────────────── #
# Live symbol search (autocomplete) — Yahoo /v1/finance/search               #
# ─────────────────────────────────────────────────────────────────────────── #

# Map Yahoo quoteType → a short, friendly label for the dropdown.
_TYPE_LABEL = {
    "EQUITY": "Stock",
    "ETF": "ETF",
    "MUTUALFUND": "Fund",
    "INDEX": "Index",
    "CURRENCY": "FX",
    "CRYPTOCURRENCY": "Crypto",
    "FUTURE": "Future",
    "OPTION": "Option",
}


def search_symbols(query: str, limit: int = 12) -> list[dict]:
    """Live symbol search across global exchanges via Yahoo's search endpoint.

    Powers the search-as-you-type box: a user types a company name or a partial
    ticker and gets back matching symbols with their exchange and instrument
    type.  Yahoo's search index spans essentially every listing it tracks
    worldwide — US (NYSE/NASDAQ/AMEX), London, Euronext, Toronto, Hong Kong,
    Tokyo, Frankfurt, Australia, India, etc. — plus ETFs, indices, FX and crypto.

    Routed through the browser-impersonating YfData session so it works from
    Render's shared cloud IP.  Returns a list of:
        {symbol, name, exchange, type}
    Empty list on an empty query or any failure (never raises)."""
    q = (query or "").strip()
    if not q:
        return []
    quoted = urllib.parse.quote(q)
    for host in ("query2", "query1"):
        url = (
            f"https://{host}.finance.yahoo.com/v1/finance/search?"
            f"q={quoted}&quotesCount={limit}&newsCount=0&listsCount=0&"
            "enableFuzzyQuery=false&quotesQueryId=tss_match_phrase_query"
        )
        data = _yf_get_json(url)
        quotes = data.get("quotes") or []
        if not quotes:
            continue
        out: list[dict] = []
        for item in quotes:
            sym = (item or {}).get("symbol")
            if not sym:
                continue
            qtype = (item.get("quoteType") or "").upper()
            out.append({
                "symbol": sym.upper(),
                "name": item.get("longname") or item.get("shortname") or sym,
                "exchange": item.get("exchDisp") or item.get("exchange") or "",
                "type": _TYPE_LABEL.get(qtype, item.get("typeDisp") or qtype.title()),
            })
            if len(out) >= limit:
                break
        if out:
            return out
    return []


# ─────────────────────────────────────────────────────────────────────────── #
# Yahoo quoteSummary — rich fundamentals + analyst data in one call           #
# ─────────────────────────────────────────────────────────────────────────── #
_SUMMARY_MODULES = (
    "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile"
)


def _raw(v):
    """Unwrap a Yahoo {"raw": n, "fmt": "…"} wrapper or return v unchanged."""
    if isinstance(v, dict) and "raw" in v:
        return v["raw"]
    return v


def _yf_quote_summary(ticker: str) -> dict:
    """Call Yahoo's /v10/finance/quoteSummary endpoint directly.

    This endpoint returns the same rich payload as ``Ticker.info`` but uses a
    different code-path that is less aggressively rate-limited on Render's shared
    IPs.  All numeric values are returned as {"raw": n, "fmt": "…"} wrappers
    which we unwrap, producing a flat dict with the same key names that
    ``get_market_snapshot`` already looks up in the `info` dict.

    Modules pulled:
      price              — regularMarketPrice, marketCap, currency, name
      summaryDetail      — beta, 52-week hi/lo, previousClose, dividendYield
      defaultKeyStatistics — trailingPE, forwardPE, pegRatio
      financialData      — profitMargins, revenueGrowth, earningsGrowth,
                           analyst target / recommendation / # analysts
      assetProfile       — sector, industry
    """
    quoted = urllib.parse.quote(ticker)
    modules = urllib.parse.quote(_SUMMARY_MODULES)
    for host in ("query2", "query1"):
        url = (
            f"https://{host}.finance.yahoo.com/v10/finance/quoteSummary/"
            f"{quoted}?modules={modules}"
        )
        data = _yf_auth_json(url)
        result_list = (data.get("quoteSummary", {}) or {}).get("result") or []
        if not result_list:
            continue
        merged: dict = {}
        # Flatten all modules into a single dict; later modules override earlier
        # ones only for missing keys (summaryDetail.beta vs defaultKeyStatistics.beta
        # — both are fine; we take whichever arrives first).
        for module_data in result_list[0].values():
            if not isinstance(module_data, dict):
                continue
            for k, v in module_data.items():
                if k not in merged and v is not None:
                    merged[k] = _raw(v)
        if merged:
            return merged
    return {}


# ─────────────────────────────────────────────────────────────────────────── #
# Alpha Vantage OVERVIEW — optional fundamentals + analyst enrichment         #
# Set ALPHAVANTAGE_API_KEY to activate. Free tier: 25 req/day.               #
# ─────────────────────────────────────────────────────────────────────────── #

# Maps Alpha Vantage OVERVIEW keys → yfinance-style info keys used downstream
_AV_KEY_MAP = {
    "MarketCapitalization": "marketCap",
    "TrailingPE":           "trailingPE",
    "ForwardPE":            "forwardPE",
    "PEGRatio":             "pegRatio",
    "Beta":                 "beta",
    "52WeekHigh":           "fiftyTwoWeekHigh",
    "52WeekLow":            "fiftyTwoWeekLow",
    "DividendYield":        "dividendYield",
    "ProfitMargin":         "profitMargins",
    "QuarterlyEarningsGrowthYOY": "earningsGrowth",
    "QuarterlyRevenueGrowthYOY":  "revenueGrowth",
    "AnalystTargetPrice":   "targetMeanPrice",
    "Sector":               "sector",
    "Industry":             "industry",
    "Name":                 "longName",
    "Symbol":               "symbol",
    # analyst rating counts — aggregate below
    "AnalystRatingStrongBuy":  "_av_strong_buy",
    "AnalystRatingBuy":        "_av_buy",
    "AnalystRatingHold":       "_av_hold",
    "AnalystRatingSell":       "_av_sell",
    "AnalystRatingStrongSell": "_av_strong_sell",
}


def _alpha_vantage_overview(ticker: str) -> dict:
    """Fetch the Alpha Vantage OVERVIEW endpoint and return an info-compatible dict.

    Activated when ALPHAVANTAGE_API_KEY is set. Free tier: 25 API calls/day.
    Returns {} if the key is not set or the call fails."""
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    if not key:
        return {}
    try:
        qs = urllib.parse.urlencode(
            {"function": "OVERVIEW", "symbol": ticker, "apikey": key}
        )
        with urllib.request.urlopen(
            f"https://www.alphavantage.co/query?{qs}", timeout=15
        ) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        if not raw or "Symbol" not in raw:
            return {}
        out: dict = {}
        for av_key, info_key in _AV_KEY_MAP.items():
            v = raw.get(av_key)
            if v in (None, "", "None", "N/A", "-"):
                continue
            try:
                numeric = float(v)
                out[info_key] = numeric
            except (TypeError, ValueError):
                out[info_key] = v  # keep string (sector, industry, name)
        # Derive analyst counts + recommendation_mean from rating breakdown
        try:
            sb = float(raw.get("AnalystRatingStrongBuy", 0) or 0)
            b  = float(raw.get("AnalystRatingBuy",       0) or 0)
            h  = float(raw.get("AnalystRatingHold",      0) or 0)
            s  = float(raw.get("AnalystRatingSell",      0) or 0)
            ss = float(raw.get("AnalystRatingStrongSell",0) or 0)
            total = sb + b + h + s + ss
            if total > 0:
                out["numberOfAnalystOpinions"] = int(total)
                # Weighted mean: 1=strong buy … 5=strong sell
                mean = (sb*1 + b*2 + h*3 + s*4 + ss*5) / total
                out["recommendationMean"] = round(mean, 2)
                if mean <= 1.5:
                    out["recommendationKey"] = "strong_buy"
                elif mean <= 2.5:
                    out["recommendationKey"] = "buy"
                elif mean <= 3.5:
                    out["recommendationKey"] = "hold"
                elif mean <= 4.5:
                    out["recommendationKey"] = "sell"
                else:
                    out["recommendationKey"] = "strong_sell"
        except (TypeError, ValueError):
            pass
        # Remove internal keys
        for k in list(out):
            if k.startswith("_av_"):
                del out[k]
        return out
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────── #
# Financial Modeling Prep — optional fundamentals enrichment                  #
# Set FMP_API_KEY to activate. Free tier: 250 req/day.                       #
# ─────────────────────────────────────────────────────────────────────────── #

def _fmp_fundamentals(ticker: str) -> dict:
    """Fetch FMP profile + key-metrics-TTM and return an info-compatible dict.

    Activated when FMP_API_KEY is set. Free tier: 250 API calls/day.
    FMP profile covers: price, marketCap, beta, 52w range, sector/industry.
    Key-metrics-TTM covers: P/E, P/B, EV/EBITDA, profit margin, ROE, ROA.
    Returns {} if the key is not set or the call fails."""
    key = os.environ.get("FMP_API_KEY", "").strip()
    if not key:
        return {}
    base = "https://financialmodelingprep.com/api/v3"
    out: dict = {}
    try:
        # --- profile ---
        url = f"{base}/profile/{urllib.parse.quote(ticker)}?apikey={key}"
        req = urllib.request.Request(url, headers=_YF_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as r:
            profiles = json.loads(r.read().decode("utf-8")) or []
        p = profiles[0] if profiles else {}
        _fmp_set(out, p, "companyName",     "longName")
        _fmp_set(out, p, "sector",          "sector")
        _fmp_set(out, p, "industry",        "industry")
        _fmp_set(out, p, "mktCap",          "marketCap",    numeric=True)
        _fmp_set(out, p, "price",           "regularMarketPrice", numeric=True)
        _fmp_set(out, p, "beta",            "beta",         numeric=True)
        _fmp_set(out, p, "lastDiv",         "dividendYield",numeric=True)
        _fmp_set(out, p, "currency",        "currency")
        _fmp_set(out, p, "exchangeShortName","fullExchangeName")
    except Exception:
        pass
    try:
        # --- key metrics TTM ---
        url = f"{base}/key-metrics-ttm/{urllib.parse.quote(ticker)}?apikey={key}"
        req = urllib.request.Request(url, headers=_YF_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as r:
            km_list = json.loads(r.read().decode("utf-8")) or []
        km = km_list[0] if km_list else {}
        _fmp_set(out, km, "peRatioTTM",             "trailingPE",   numeric=True)
        _fmp_set(out, km, "pegRatioTTM",            "pegRatio",     numeric=True)
        _fmp_set(out, km, "netProfitMarginTTM",     "profitMargins",numeric=True)
        _fmp_set(out, km, "revenueGrowthTTM",       "revenueGrowth",numeric=True)
    except Exception:
        pass
    return out


def _fmp_set(out: dict, src: dict, src_key: str, dst_key: str,
             numeric: bool = False) -> None:
    """Copy src[src_key] into out[dst_key] only when the destination is empty."""
    if out.get(dst_key) is not None:
        return
    v = src.get(src_key)
    if v in (None, "", "N/A"):
        return
    if numeric:
        try:
            out[dst_key] = float(v)
            return
        except (TypeError, ValueError):
            return
    out[dst_key] = v


# --- batch quote ----------------------------------------------------------- #
# Yahoo's /v7/finance/quote returns price, change, market cap, P/E, name AND
# sector for MANY symbols in a SINGLE request. That matters in production:
# per-ticker ``Ticker.info`` is the most aggressively rate-limited Yahoo
# endpoint from a shared cloud IP, so building peer rows from one batch quote
# (instead of one ``.info`` call per peer) is far more resilient on Render.
_QUOTE_FIELDS = (
    "symbol,shortName,longName,regularMarketPrice,regularMarketPreviousClose,"
    "regularMarketChangePercent,marketCap,trailingPE,forwardPE,beta,"
    "fiftyTwoWeekHigh,fiftyTwoWeekLow,sector,industry,currency,fullExchangeName,"
    "trailingAnnualDividendYield,priceToBook,epsTrailingTwelveMonths,epsForward,"
    "profitMargins,pegRatio"
)


def _yf_quote_batch(symbols: list[str]) -> dict[str, dict]:
    """Fetch quote rows for many symbols at once; keyed by upper-case symbol."""
    syms = [s.upper() for s in symbols if s]
    if not syms:
        return {}
    out: dict[str, dict] = {}
    # Yahoo caps the symbol list length; chunk to be safe.
    for i in range(0, len(syms), 40):
        chunk = syms[i : i + 40]
        joined = urllib.parse.quote(",".join(chunk))
        for host in ("query2", "query1"):
            url = (
                f"https://{host}.finance.yahoo.com/v7/finance/quote?"
                f"symbols={joined}&fields={urllib.parse.quote(_QUOTE_FIELDS)}"
            )
            data = _yf_auth_json(url)
            results = (data.get("quoteResponse", {}) or {}).get("result") or []
            if results:
                for q in results:
                    sym = (q or {}).get("symbol")
                    if sym:
                        out[sym.upper()] = q
                break
    return out


def _chart_meta(ticker: str) -> dict:
    """Currency / exchange / name / price from the /v8/chart meta block.

    The chart endpoint is the SAME one ``Ticker.history()`` uses and is the most
    reliable Yahoo route — it keeps working from a shared cloud IP even when the
    authenticated /quote and /quoteSummary endpoints are rate-limited. We use it
    to GUARANTEE the correct CURRENCY (and exchange/name) are always shown, so a
    Singapore stock never falls back to a misleading 'USD' default just because
    the fundamentals call was throttled. Returns {} on failure."""
    su = (ticker or "").upper()
    if not su:
        return {}
    for host in ("query2", "query1"):
        url = (
            f"https://{host}.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.parse.quote(su)}?range=1d&interval=1d"
        )
        data = _yf_get_json(url)
        meta = (
            ((data.get("chart", {}) or {}).get("result") or [{}])[0] or {}
        ).get("meta", {}) or {}
        if meta.get("currency") or meta.get("regularMarketPrice"):
            return {
                "currency": meta.get("currency"),
                "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
                "name": meta.get("longName") or meta.get("shortName"),
                "price": meta.get("regularMarketPrice"),
                "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
                # Yahoo's instrumentType drives asset-class detection downstream
                # (EQUITY / FUTURE / CURRENCY / CRYPTOCURRENCY / INDEX / ETF).
                "instrument_type": meta.get("instrumentType"),
            }
    return {}


def _yf_chart_quote(sym: str) -> dict:
    """Last-ditch live price via the ``/v8/finance/chart`` endpoint.

    The chart endpoint (the same one ``Ticker.history()`` uses) stays available
    from Render's shared IP even during windows when ``/v7/quote`` and
    ``Ticker.info`` are rate-limited. It only carries price/prev-close — not
    market cap / P/E / sector — so it is used purely to keep a peer row's price
    and change-% populated when the batch quote misses that symbol."""
    su = (sym or "").upper()
    if not su:
        return {}
    for host in ("query2", "query1"):
        url = (
            f"https://{host}.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.parse.quote(su)}?range=5d&interval=1d"
        )
        data = _yf_get_json(url)
        meta = (
            ((data.get("chart", {}) or {}).get("result") or [{}])[0] or {}
        ).get("meta", {}) or {}
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is not None:
            chg = None
            try:
                if prev:
                    chg = (float(price) - float(prev)) / float(prev) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                chg = None
            return {
                "regularMarketPrice": price,
                "regularMarketPreviousClose": prev,
                "regularMarketChangePercent": chg,
                "shortName": meta.get("shortName") or meta.get("longName"),
            }
    return {}


def _row_from_quote(sym: str, q: dict) -> dict:
    """Build a compact, JSON-safe peer row from a /v7/quote result entry."""
    price = q.get("regularMarketPrice")
    chg = q.get("regularMarketChangePercent")
    return {
        "ticker": sym.upper(),
        "name": q.get("shortName") or q.get("longName") or sym.upper(),
        "price": _round(price, 2),
        "change_pct": _round(chg, 2),
        "market_cap": _num(q.get("marketCap")),
        "trailing_pe": _round(q.get("trailingPE")),
        # profitMargins is now in the expanded _QUOTE_FIELDS; may still be None
        # for some symbols if Yahoo omits it from the batch endpoint.
        "profit_margin": _round(q.get("profitMargins"), 4),
        "revenue": None,
        # Per-peer currency so the UI labels each row correctly (peers can be
        # cross-listed in a different currency than the searched symbol).
        "currency": q.get("currency"),
    }


# --- tiny in-process TTL cache -------------------------------------------- #
# A successful peer lookup is cached briefly so a subsequent request can survive
# a Yahoo rate-limit window (when the live call would otherwise return empty).
_PEERS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_PEERS_TTL = 21600.0  # 6 hours — long enough to bridge Render-IP throttle windows


def _peers_cache_get(ticker: str) -> list[dict] | None:
    entry = _PEERS_CACHE.get(ticker.upper())
    if entry and (time.time() - entry[0]) < _PEERS_TTL:
        return entry[1]
    return None


def _peers_cache_put(ticker: str, rows: list[dict]) -> None:
    if rows:
        _PEERS_CACHE[ticker.upper()] = (time.time(), rows)


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
    # _yf_get_json prefers the browser-impersonating YfData session (works from
    # Render's cloud IP) and falls back to plain urllib (works locally).
    for host in ("query2", "query1"):
        url = (
            f"https://{host}.finance.yahoo.com/v6/finance/"
            f"recommendationsbysymbol/{quoted}"
        )
        syms = _parse_recommended_symbols(_yf_get_json(url))
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

    Peer rows are then built from a SINGLE batch ``/v7/finance/quote`` call rather
    than one ``Ticker.info`` per peer. ``Ticker.info`` is the most aggressively
    rate-limited Yahoo endpoint from a shared cloud IP (Render), so the batch
    quote is what keeps peers populated in production. A short in-process cache
    lets a result survive a transient rate-limit window. ``_peer_snapshot`` (the
    old per-ticker ``.info`` path) is kept only as a last-ditch fallback.

    The queried ticker is always the first row. Capped to ``limit``."""
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

    wanted = [tu, *peers_syms]

    # Primary path: one batch quote for the searched symbol + all peers.
    quotes = _yf_quote_batch(wanted)
    rows: list[dict] = []
    for sym in wanted:
        q = quotes.get(sym)
        if q:
            rows.append(_row_from_quote(sym, q))
        else:
            # The batch quote missed this symbol (partial throttling). Try the
            # resilient chart endpoint so the row still carries a live price.
            cq = _yf_chart_quote(sym)
            if cq:
                rows.append(_row_from_quote(sym, cq))

    # If we got nothing at all (whole quote+chart path throttled), fall back to
    # the per-ticker ``.info`` snapshot for whatever we can still scrape.
    if not rows:
        self_row = _peer_snapshot(ticker)
        if self_row:
            rows.append(self_row)
        for s in peers_syms:
            row = _peer_snapshot(s)
            if row:
                rows.append(row)

    # If we have real peers (more than just the self row), cache and return.
    if len(rows) > 1:
        _peers_cache_put(ticker, rows)
        return rows

    # Last resort: serve a recent cached result so the table isn't empty while
    # Yahoo is rate-limiting this IP. This is what lets a previously-seen ticker
    # survive a throttle window even when every live path fails.
    cached = _peers_cache_get(ticker)
    if cached and len(cached) > len(rows):
        return cached
    return rows


def _get_peers_safe(ticker: str, base_info: dict, limit: int = 6) -> list[dict]:
    """``_get_peers`` wrapper that never raises and always prefers a non-empty
    result — serving cached peers if a live lookup throws or comes back empty."""
    try:
        rows = _get_peers(ticker, base_info, limit)
    except Exception:
        rows = []
    if len(rows) > 1:
        return rows
    cached = _peers_cache_get(ticker)
    if cached and len(cached) > len(rows):
        return cached
    return rows


def _alpha_vantage_quote(ticker: str) -> float | None:
    """Optional fallback live quote via Alpha Vantage GLOBAL_QUOTE endpoint."""
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


# ─────────────────────────────────────────────────────────────────────────── #
# Asset-class context — the NATIVE scorecard for commodities, FX, and crypto.  #
# Equities get P/E, analyst targets, etc.; those are meaningless for gold or    #
# EUR/USD. Instead we attach a `context` block with the metrics that actually   #
# drive each asset class, sourced as follows:                                   #
#   Tier 1 (free, no key): Yahoo macro symbols (DXY, ^TNX, ^VIX) + values       #
#           COMPUTED from the price series we already have (realized vol,        #
#           range position, seasonality, gold/silver ratio).                    #
#   Tier 2 (free, keyless): CoinGecko (mcap/supply/dominance), Binance public   #
#           (funding/OI), alternative.me (fear & greed) for crypto.             #
#   Tier 3 (opt-in / keyed): CFTC COT positioning (ENABLE_COT), FRED policy     #
#           rates (FRED_API_KEY), EIA energy inventories (EIA_API_KEY).         #
# Every fetch is best-effort and time-boxed — a missing source just omits its   #
# field rather than failing the request.                                        #
# ─────────────────────────────────────────────────────────────────────────── #

def _http_json(url: str, timeout: int = 8, headers: dict | None = None):
    """GET arbitrary JSON (stdlib only). Returns None on any error. Unlike
    ``_yf_json`` this does NOT proxify — used for non-Yahoo sources (CoinGecko,
    Binance, CFTC, FRED, EIA, alternative.me)."""
    try:
        req = urllib.request.Request(url, headers=headers or _YF_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _spot(sym: str):
    """Last price for a Yahoo symbol via the always-available chart endpoint
    (works for ^TNX, ^VIX, DX-Y.NYB, GC=F, etc. from a shared cloud IP)."""
    try:
        return _num(_yf_chart_quote(sym).get("regularMarketPrice"))
    except Exception:
        return None


def _spots(symbols: dict, timeout: int = 6) -> dict:
    """Fetch several Yahoo spot prices concurrently. {label: symbol} -> {label: price}."""
    futs = {k: _NET_POOL.submit(_spot, v) for k, v in symbols.items()}
    out: dict = {}
    for k, fut in futs.items():
        try:
            out[k] = fut.result(timeout=timeout)
        except Exception:
            out[k] = None
    return out


def _realized_vol(closes: list, window: int = 30):
    """Annualized realized volatility (%) from the last `window` daily log returns."""
    xs = [c for c in (closes or []) if c]
    if len(xs) < 3:
        return None
    rets = [math.log(xs[i] / xs[i - 1]) for i in range(1, len(xs)) if xs[i - 1] > 0]
    tail = rets[-window:]
    if len(tail) < 2:
        return None
    mean = sum(tail) / len(tail)
    var = sum((r - mean) ** 2 for r in tail) / (len(tail) - 1)
    return round((var ** 0.5) * (252 ** 0.5) * 100, 1)


def _range_position(closes: list):
    """Where the latest price sits in its trailing range (0 = low, 100 = high)."""
    xs = [c for c in (closes or []) if c]
    if len(xs) < 2:
        return None
    lo, hi, last = min(xs), max(xs), xs[-1]
    if hi <= lo:
        return None
    return round((last - lo) / (hi - lo) * 100, 1)


_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _monthly_seasonality(series: dict):
    """Average month-over-month return by calendar month, from the price series.

    Commodities have real, physically-driven seasonality (nat-gas winter, gasoline
    summer, ags around harvest). Returns the current month's historical bias plus
    the best/worst months, or None if there isn't enough history."""
    dates = series.get("date") or []
    closes = series.get("close") or []
    if len(dates) != len(closes) or len(closes) < 40:
        return None
    by_month: dict[str, float] = {}  # 'YYYY-MM' -> last close that month
    for d, c in zip(dates, closes):
        if c is not None and isinstance(d, str) and len(d) >= 7:
            by_month[d[:7]] = c
    keys = sorted(by_month)
    if len(keys) < 8:
        return None
    buckets: dict[int, list] = {}
    for i in range(1, len(keys)):
        prev, cur = by_month[keys[i - 1]], by_month[keys[i]]
        if prev and prev > 0:
            mo = int(keys[i][5:7])
            buckets.setdefault(mo, []).append((cur / prev - 1) * 100)
    if not buckets:
        return None
    avg = {mo: sum(v) / len(v) for mo, v in buckets.items()}
    best = max(avg, key=avg.get)
    worst = min(avg, key=avg.get)
    cur_mo = datetime.now(timezone.utc).month
    return {
        "current_month": _MONTHS[cur_mo],
        "current_month_avg_pct": round(avg[cur_mo], 2) if cur_mo in avg else None,
        "best_month": _MONTHS[best], "best_avg_pct": round(avg[best], 2),
        "worst_month": _MONTHS[worst], "worst_avg_pct": round(avg[worst], 2),
    }


# ── Tier 3: CFTC Commitments of Traders (opt-in via ENABLE_COT) ──────────────
# Keyless public Socrata endpoint. Maps a Yahoo symbol to the CFTC market name
# prefix; managed-money / non-commercial net positioning is a classic
# contrarian/positioning gauge. Cached 12h (the report is weekly).
_COT_RESOURCE = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_COT_MAP = {
    "GC=F": "GOLD", "SI=F": "SILVER", "HG=F": "COPPER- #1",
    "PL=F": "PLATINUM", "PA=F": "PALLADIUM",
    "CL=F": "CRUDE OIL, LIGHT SWEET", "NG=F": "NATURAL GAS", "BZ=F": "BRENT",
    "RB=F": "GASOLINE RBOB", "HO=F": "#2 HEATING OIL",
    "ZC=F": "CORN", "ZW=F": "WHEAT-SRW", "ZS=F": "SOYBEANS",
    "ZM=F": "SOYBEAN MEAL", "ZL=F": "SOYBEAN OIL",
    "KC=F": "COFFEE", "SB=F": "SUGAR NO. 11", "CT=F": "COTTON NO. 2", "CC=F": "COCOA",
    "EURUSD=X": "EURO FX", "JPY=X": "JAPANESE YEN", "GBPUSD=X": "BRITISH POUND",
    "AUDUSD=X": "AUSTRALIAN DOLLAR", "USDCAD=X": "CANADIAN DOLLAR",
    "USDCHF=X": "SWISS FRANC", "NZDUSD=X": "NEW ZEALAND DOLLAR",
    "MXN=X": "MEXICAN PESO",
}
_COT_CACHE: dict[str, tuple[float, dict]] = {}
_COT_TTL = 43200.0  # 12h


def _cot_positioning(ticker: str):
    """Latest non-commercial (large-spec) net positioning from the CFTC COT report.
    Opt-in: only runs when ENABLE_COT is truthy. Returns None when off/unmapped."""
    if (os.environ.get("ENABLE_COT") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    prefix = _COT_MAP.get(ticker.upper())
    if not prefix:
        return None
    hit = _COT_CACHE.get(ticker.upper())
    if hit and (time.time() - hit[0]) < _COT_TTL:
        return hit[1]
    token = os.environ.get("CFTC_APP_TOKEN", "").strip()
    safe = prefix.replace("'", "''")
    where = urllib.parse.quote(f"upper(market_and_exchange_names) like '{safe}%'")
    url = (
        f"{_COT_RESOURCE}?$where={where}"
        "&$order=report_date_as_yyyy_mm_dd DESC&$limit=1"
        + (f"&$$app_token={urllib.parse.quote(token)}" if token else "")
    )
    data = _http_json(url, timeout=8)
    if not isinstance(data, list) or not data:
        return None
    row = data[0]
    try:
        long_ = float(row.get("noncomm_positions_long_all") or 0)
        short_ = float(row.get("noncomm_positions_short_all") or 0)
        oi = float(row.get("open_interest_all") or 0)
    except (TypeError, ValueError):
        return None
    net = long_ - short_
    out = {
        "market": row.get("market_and_exchange_names"),
        "report_date": (row.get("report_date_as_yyyy_mm_dd") or "")[:10],
        "noncomm_long": int(long_), "noncomm_short": int(short_),
        "noncomm_net": int(net),
        "net_pct_oi": round(net / oi * 100, 1) if oi else None,
        "bias": "net long" if net > 0 else "net short" if net < 0 else "flat",
    }
    _COT_CACHE[ticker.upper()] = (time.time(), out)
    return out


# ── Tier 3: FRED central-bank policy rates (keyed via FRED_API_KEY) ──────────
_FRED_POLICY = {  # currency -> FRED series id for its policy/main rate
    "USD": "DFEDTARU",   # Fed funds target (upper bound)
    "EUR": "ECBDFR",     # ECB deposit facility rate
    # Add more currencies here (verified FRED series ids); unmapped legs are
    # simply omitted from the differential rather than guessed.
}
_FRED_CACHE: dict[str, tuple[float, float]] = {}
_FRED_TTL = 43200.0


def _fred_latest(series_id: str):
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key or not series_id:
        return None
    hit = _FRED_CACHE.get(series_id)
    if hit and (time.time() - hit[0]) < _FRED_TTL:
        return hit[1]
    url = (
        "https://api.stlouisfed.org/fred/series/observations?"
        f"series_id={urllib.parse.quote(series_id)}&api_key={urllib.parse.quote(key)}"
        "&file_type=json&sort_order=desc&limit=1"
    )
    data = _http_json(url, timeout=8)
    try:
        val = float(((data or {}).get("observations") or [{}])[0].get("value"))
    except (TypeError, ValueError):
        return None
    _FRED_CACHE[series_id] = (time.time(), val)
    return val


def _fred_policy_rates(base: str | None, quote: str | None):
    """Policy rate for each leg of an FX pair (+ the differential), via FRED.
    Returns None when FRED_API_KEY isn't set or neither leg resolves."""
    if not os.environ.get("FRED_API_KEY", "").strip():
        return None
    rb = _fred_latest(_FRED_POLICY.get((base or "").upper(), ""))
    rq = _fred_latest(_FRED_POLICY.get((quote or "").upper(), ""))
    if rb is None and rq is None:
        return None
    out = {"base_rate": rb, "quote_rate": rq}
    if rb is not None and rq is not None:
        out["differential"] = round(rb - rq, 2)
    return out


# ── Tier 3: EIA weekly inventories for energy (keyed via EIA_API_KEY) ────────
_EIA_SERIES = {  # energy ticker -> EIA v2 series id (weekly stocks)
    "CL=F": "PET.WCESTUS1.W",   # crude oil ex-SPR, weekly
    "RB=F": "PET.WGTSTUS1.W",   # total motor gasoline
    "HO=F": "PET.WDISTUS1.W",   # distillate (heating oil/diesel)
    "NG=F": "NG.NW2_EPG0_SWO_R48_BCF.W",  # working gas in storage, lower-48
}
_EIA_CACHE: dict[str, tuple[float, dict]] = {}
_EIA_TTL = 43200.0


def _eia_inventory(ticker: str):
    key = os.environ.get("EIA_API_KEY", "").strip()
    sid = _EIA_SERIES.get(ticker.upper())
    if not key or not sid:
        return None
    hit = _EIA_CACHE.get(ticker.upper())
    if hit and (time.time() - hit[0]) < _EIA_TTL:
        return hit[1]
    url = (
        "https://api.eia.gov/v2/seriesid/"
        f"{urllib.parse.quote(sid)}?api_key={urllib.parse.quote(key)}"
    )
    data = _http_json(url, timeout=10)
    try:
        rows = ((data or {}).get("response") or {}).get("data") or []
        latest, prev = rows[0], rows[1]
        cur_v, prev_v = float(latest["value"]), float(prev["value"])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    out = {
        "period": latest.get("period"),
        "value": cur_v,
        "wow_change": round(cur_v - prev_v, 1),
        "units": latest.get("units") or "",
    }
    _EIA_CACHE[ticker.upper()] = (time.time(), out)
    return out


# ── Tier 2: crypto (CoinGecko + Binance public + alternative.me — keyless) ───
def _coingecko_market(base: str) -> dict:
    sym = base.lower()
    data = _http_json(
        "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
        f"&symbols={urllib.parse.quote(sym)}&price_change_percentage=24h"
    )
    if isinstance(data, list) and data:
        d = data[0]
        return {
            "market_cap": _num(d.get("market_cap")),
            "circulating_supply": _num(d.get("circulating_supply")),
            "max_supply": _num(d.get("max_supply")) or _num(d.get("total_supply")),
            "ath_change_pct": _round(d.get("ath_change_percentage"), 1),
        }
    return {}


def _btc_dominance():
    g = _http_json("https://api.coingecko.com/api/v3/global")
    try:
        return round(float(g["data"]["market_cap_percentage"]["btc"]), 1)
    except (TypeError, ValueError, KeyError):
        return None


def _binance_perp(base: str) -> dict:
    sym = base.upper() + "USDT"
    out: dict = {}
    fr = _http_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}")
    if isinstance(fr, dict) and fr.get("lastFundingRate") is not None:
        try:
            out["funding_rate_pct"] = round(float(fr["lastFundingRate"]) * 100, 4)
        except (TypeError, ValueError):
            pass
    oi = _http_json(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={sym}")
    if isinstance(oi, dict) and oi.get("openInterest") is not None:
        out["open_interest"] = _num(oi["openInterest"])
    return out


def _fear_greed():
    d = _http_json("https://api.alternative.me/fng/?limit=1")
    try:
        item = d["data"][0]
        return {"value": int(item["value"]), "label": item.get("value_classification")}
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def _parse_fx(symbol: str) -> tuple[str | None, str | None]:
    """Split a Yahoo FX symbol into (base, quote). EURUSD=X -> (EUR, USD);
    a 3-letter form like JPY=X means USD/JPY -> (USD, JPY)."""
    s = (symbol or "").upper().replace("=X", "")
    if len(s) == 6:
        return s[:3], s[3:]
    if len(s) == 3:
        return "USD", s
    return None, None


def _commodity_context(ticker: str, series: dict) -> dict:
    closes = series.get("close") or []
    spots = _spots({"dxy": "DX-Y.NYB", "ust10y": "^TNX", "vix": "^VIX",
                    "gold": "GC=F", "silver": "SI=F"})
    gsr = None
    if spots.get("gold") and spots.get("silver"):
        gsr = round(spots["gold"] / spots["silver"], 2)
    ctx = {
        "asset_class": "commodity",
        "dxy": _round(spots.get("dxy"), 2),
        "ust10y": _round(spots.get("ust10y"), 2),
        "vix": _round(spots.get("vix"), 2),
        "gold_silver_ratio": gsr,
        "realized_vol_30d": _realized_vol(closes, 30),
        "range_position_pct": _range_position(closes),
        "seasonality": _monthly_seasonality(series),
    }
    cot = _cot_positioning(ticker)
    if cot:
        ctx["cot"] = cot
    inv = _eia_inventory(ticker)
    if inv:
        ctx["inventory"] = inv
    return ctx


def _fx_context(ticker: str, series: dict) -> dict:
    base, quote = _parse_fx(ticker)
    closes = series.get("close") or []
    spots = _spots({"dxy": "DX-Y.NYB", "ust10y": "^TNX"})
    ctx = {
        "asset_class": "fx",
        "base": base, "quote": quote,
        "dxy": _round(spots.get("dxy"), 2),
        "us10y": _round(spots.get("ust10y"), 2),
        "realized_vol_30d": _realized_vol(closes, 30),
        "range_position_pct": _range_position(closes),
    }
    rates = _fred_policy_rates(base, quote)
    if rates:
        ctx["policy_rates"] = rates
    cot = _cot_positioning(ticker)
    if cot:
        ctx["cot"] = cot
    return ctx


def _crypto_context(ticker: str, series: dict) -> dict:
    base = ticker.upper().split("-")[0]
    closes = series.get("close") or []
    f_cg = _NET_POOL.submit(_coingecko_market, base)
    f_dom = _NET_POOL.submit(_btc_dominance)
    f_perp = _NET_POOL.submit(_binance_perp, base)
    f_fng = _NET_POOL.submit(_fear_greed)

    def g(fut, default):
        try:
            return fut.result(timeout=7)
        except Exception:
            return default

    cg = g(f_cg, {}) or {}
    supply_pct = None
    if cg.get("circulating_supply") and cg.get("max_supply"):
        try:
            supply_pct = round(cg["circulating_supply"] / cg["max_supply"] * 100, 1)
        except (TypeError, ZeroDivisionError):
            supply_pct = None
    return {
        "asset_class": "crypto",
        "base": base,
        "market_cap": cg.get("market_cap"),
        "circulating_supply": cg.get("circulating_supply"),
        "max_supply": cg.get("max_supply"),
        "supply_pct": supply_pct,
        "ath_change_pct": cg.get("ath_change_pct"),
        "btc_dominance": g(f_dom, None),
        **(g(f_perp, {}) or {}),
        "fear_greed": g(f_fng, None),
        "realized_vol_30d": _realized_vol(closes, 30),
        "range_position_pct": _range_position(closes),
    }


def build_context(asset_cls: str, ticker: str, series: dict) -> dict:
    """Asset-class-specific context block. Empty dict for equity/index/fund or on
    any error — callers attach it under snapshot['context']."""
    try:
        if asset_cls == "commodity":
            return _commodity_context(ticker, series)
        if asset_cls == "fx":
            return _fx_context(ticker, series)
        if asset_cls == "crypto":
            return _crypto_context(ticker, series)
    except Exception:
        return {}
    return {}


def _diag_entry(value, ok_fn, sample_fn=None, *, configured=True, note=None) -> dict:
    """Build one source-status row for the /api/diag/context report."""
    if not configured:
        out = {"configured": False, "ok": False}
        if note:
            out["note"] = note
        return out
    if isinstance(value, dict) and "__error__" in value:
        return {"configured": True, "ok": False, "error": value["__error__"]}
    out = {"configured": True, "ok": bool(ok_fn(value))}
    if out["ok"] and sample_fn:
        try:
            out["sample"] = sample_fn(value)
        except Exception:
            pass
    if not out["ok"] and note:
        out["note"] = note
    return out


def diag_sources() -> dict:
    """Ping each external context source and report status per source.

    Powers /api/diag/context so the optional integrations can be verified without
    hunting through the UI. Probes with representative symbols (BTC for crypto,
    GC=F for COT, the Fed funds series for FRED, crude stocks for EIA). Tier-2
    crypto sources are keyless; Tier-3 (COT/FRED/EIA) only probe when enabled."""
    cot_on = (os.environ.get("ENABLE_COT") or "").strip().lower() in {"1", "true", "yes", "on"}
    fred_key = bool(os.environ.get("FRED_API_KEY", "").strip())
    eia_key = bool(os.environ.get("EIA_API_KEY", "").strip())

    jobs = {
        "coingecko": _NET_POOL.submit(_coingecko_market, "btc"),
        "binance": _NET_POOL.submit(_binance_perp, "btc"),
        "fear_greed": _NET_POOL.submit(_fear_greed),
    }
    if cot_on:
        jobs["cot"] = _NET_POOL.submit(_cot_positioning, "GC=F")
    if fred_key:
        jobs["fred"] = _NET_POOL.submit(_fred_latest, "DFEDTARU")
    if eia_key:
        jobs["eia"] = _NET_POOL.submit(_eia_inventory, "CL=F")

    got: dict = {}
    for k, fut in jobs.items():
        try:
            got[k] = fut.result(timeout=12)
        except Exception as exc:
            got[k] = {"__error__": str(exc)[:200]}

    return {
        "tier2_keyless": {
            "coingecko": _diag_entry(
                got.get("coingecko"),
                lambda v: isinstance(v, dict) and v.get("market_cap") is not None,
                lambda v: {"btc_market_cap": v.get("market_cap")},
            ),
            "binance": _diag_entry(
                got.get("binance"),
                lambda v: isinstance(v, dict) and "funding_rate_pct" in v,
                lambda v: {"btc_funding_pct": v.get("funding_rate_pct"),
                           "open_interest": v.get("open_interest")},
                note="Binance public futures API may geo-block some data-center IPs; "
                     "everything else is unaffected.",
            ),
            "fear_greed": _diag_entry(
                got.get("fear_greed"),
                lambda v: isinstance(v, dict) and v.get("value") is not None,
                lambda v: v,
            ),
        },
        "tier3_keyed": {
            "cot": _diag_entry(
                got.get("cot"),
                lambda v: isinstance(v, dict) and v.get("noncomm_net") is not None,
                lambda v: {"market": v.get("market"), "report_date": v.get("report_date"),
                           "bias": v.get("bias")},
                configured=cot_on,
                note="set ENABLE_COT=1 to enable" if not cot_on
                else "enabled but probe returned no data (CFTC throttled or symbol unmapped)",
            ),
            "fred": _diag_entry(
                got.get("fred"),
                lambda v: isinstance(v, (int, float)),
                lambda v: {"fed_funds_upper_pct": v},
                configured=fred_key,
                note="set FRED_API_KEY to enable" if not fred_key
                else "key set but probe failed (check the key)",
            ),
            "eia": _diag_entry(
                got.get("eia"),
                lambda v: isinstance(v, dict) and v.get("value") is not None,
                lambda v: {"period": v.get("period"), "crude_stocks": v.get("value"),
                           "units": v.get("units")},
                configured=eia_key,
                note="set EIA_API_KEY to enable" if not eia_key
                else "key set but probe failed (check the key)",
            ),
        },
    }


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
        # Hard 12s budget on the price history too. A genuine "bad symbol" error
        # from get_history propagates normally (-> 404); only a real timeout
        # raises the rate-limit message, so the request can't hang the gateway.
        _hist_fut = _NET_POOL.submit(get_history, ticker)
        try:
            df = _hist_fut.result(timeout=9)
        except concurrent.futures.TimeoutError:
            raise ValueError(
                f"Market data for '{ticker}' timed out (provider is rate-limiting). "
                "Please try again in a moment."
            )

    frame = _indicator_frame(df)
    latest = _derive_latest(frame)

    # Fire the four independent network-heavy fetches CONCURRENTLY, each with a
    # hard wall-clock deadline, so the total wait is the slowest one (~13s worst
    # case) rather than their sum — and a throttled Yahoo endpoint can never push
    # the response past Render's gateway timeout (no 502s).
    #   info   — fundamentals + analyst consensus (multi-source)
    #   cmeta  — chart meta: currency/exchange/name (always-available endpoint)
    #   news   — recent headlines
    #   peers  — sector comparison table
    f_info = _NET_POOL.submit(_get_info, ticker)
    f_cmeta = _NET_POOL.submit(_chart_meta, ticker)
    f_news = _NET_POOL.submit(_get_news, ticker)
    f_peers = _NET_POOL.submit(_get_peers_safe, ticker, {})

    # All four share ONE 9s wall-clock deadline, so the whole bundle is bounded
    # no matter the collection order. Kept short on purpose: under throttle the
    # fundamentals fail regardless, and the request still has a price-history fetch
    # (<=9s) AND a downstream LLM assessment call to fit under Render's gateway
    # limit — so the data fetch must stay tight to avoid a 502.
    _deadline = time.monotonic() + 9.0

    def _await(fut, default):
        remaining = max(0.1, _deadline - time.monotonic())
        try:
            return fut.result(timeout=remaining)
        except Exception:
            return default

    info = _await(f_info, None)
    if info is None:
        _trip_breaker()  # overran → skip the slow auth path on the next request
        info = {}

    # Chart meta GUARANTEES the correct currency even when the authenticated
    # fundamental endpoints are throttled (so an SGX/KLSE/etc. stock never falls
    # back to a misleading 'USD').
    cmeta = _await(f_cmeta, {}) or {}
    if cmeta:
        if not info.get("currency") and cmeta.get("currency"):
            info["currency"] = cmeta["currency"]
        if not (info.get("fullExchangeName") or info.get("exchange")) and cmeta.get("exchange"):
            info["fullExchangeName"] = cmeta["exchange"]
        if not (info.get("longName") or info.get("shortName")) and cmeta.get("name"):
            info["longName"] = cmeta["name"]

    # --- live price: IBKR snapshot first when in use, then Yahoo, then fallbacks ---
    # All coerced via _num() so a Yahoo {"raw": ...} wrapper (seen on some ETF /
    # crypto fields) can't crash float() downstream.
    price = _num(ibkr.snapshot_price(ticker)) if used_ibkr else None
    if price is None:
        price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    if price is None:
        price = _num(cmeta.get("price"))
    if price is None:
        price = _num(_alpha_vantage_quote(ticker))
    if price is None:  # last resort: most recent close
        price = _num(latest["close"])
    prev_close = (
        _num(info.get("regularMarketPreviousClose"))
        or _num(info.get("previousClose"))
        or _num(cmeta.get("prev_close"))
    )
    day_change_pct = None
    if price and prev_close:
        day_change_pct = round((price - prev_close) / prev_close * 100, 2)

    meta = {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("currency") or "USD",
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        # Asset class drives the native scorecard (KPI panel + LLM prompt). Yahoo's
        # instrumentType from the chart meta is the signal; symbol suffix is fallback.
        "asset_class": asset_class.classify(cmeta.get("instrument_type"), ticker),
    }
    quote = {
        "price": _round(price, 2),
        "previous_close": _round(prev_close, 2),
        "day_change_pct": day_change_pct,
        "fifty_two_week_high": _round(info.get("fiftyTwoWeekHigh"), 2),
        "fifty_two_week_low": _round(info.get("fiftyTwoWeekLow"), 2),
    }
    fundamentals = {
        "market_cap": _num(info.get("marketCap")),
        "trailing_pe": _round(info.get("trailingPE")),
        "forward_pe": _round(info.get("forwardPE")),
        "peg_ratio": _round(info.get("pegRatio")),
        "profit_margin": _round(info.get("profitMargins"), 4),
        "revenue_growth": _round(info.get("revenueGrowth"), 4),
        "earnings_growth": _round(info.get("earningsGrowth"), 4),
        "beta": _round(info.get("beta")),
        "dividend_yield": _round(info.get("dividendYield"), 4),
    }
    _num_analysts = _num(info.get("numberOfAnalystOpinions"))
    analyst = {
        "recommendation": info.get("recommendationKey"),
        "recommendation_mean": _round(info.get("recommendationMean")),
        "num_analysts": int(_num_analysts) if _num_analysts is not None else None,
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

    # News + peers were dispatched concurrently above; collect them under the
    # same shared deadline (best-effort — never fail the request on these).
    news = _await(f_news, []) or []
    peers = _await(f_peers, []) or []

    # Asset-class context (commodity/fx/crypto): the native scorecard. Runs in the
    # request thread (its own network calls go to the pool with short timeouts), so
    # equities pay nothing and a slow/missing source just omits its field.
    context: dict = {}
    if meta["asset_class"] in ("commodity", "fx", "crypto"):
        context = build_context(meta["asset_class"], ticker, series) or {}

    # Build a human-readable data-source label for the status bar
    sources = []
    if used_ibkr:
        sources.append("IBKR (prices/history)")
    else:
        sources.append("Yahoo Finance" + (" (via proxy)" if _YAHOO_PROXY else ""))
    if os.environ.get("ALPHAVANTAGE_API_KEY") and _info_is_rich(info):
        sources.append("Alpha Vantage")
    if os.environ.get("FMP_API_KEY") and _info_is_rich(info):
        sources.append("FMP")
    data_source_label = " + ".join(sources)

    return {
        "meta": meta,
        "quote": quote,
        "indicators": latest,
        "fundamentals": fundamentals,
        "analyst": analyst,
        "series": series,
        "news": news,
        "peers": peers,
        "context": context,
        "data_source": data_source_label,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
