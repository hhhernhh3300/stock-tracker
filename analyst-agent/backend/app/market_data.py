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

    return {
        "meta": meta,
        "quote": quote,
        "indicators": latest,
        "fundamentals": fundamentals,
        "analyst": analyst,
        "series": series,
        "data_source": (
            "IBKR Client Portal — prices/history; Yahoo Finance — fundamentals & analyst consensus"
            if used_ibkr
            else "Yahoo Finance (prices, fundamentals, analyst consensus)"
        ),
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
