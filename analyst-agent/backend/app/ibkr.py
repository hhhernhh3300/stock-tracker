"""IBKR Client Portal Web API adapter (optional price/history source).

IBKR has no simple API-key REST endpoint. To use it you run IBKR's **Client
Portal Gateway** locally and keep an authenticated browser session open; this
module then calls the gateway at https://localhost:5000. See the README section
"Using IBKR as a data source" for setup.

Enable by setting DATA_SOURCE=ibkr (force) or DATA_SOURCE=auto (IBKR when its
gateway is authenticated, otherwise Yahoo) in backend/.env.

Design: IBKR supplies PRICE HISTORY + the LIVE QUOTE (broad equity universe,
solid coverage). Fundamentals and analyst consensus still come from Yahoo, since
IBKR's fundamental endpoints are subscription-gated and inconsistent over the API.
"""
from __future__ import annotations

import os

import pandas as pd

try:
    import requests
    import urllib3

    urllib3.disable_warnings()  # gateway serves a self-signed cert on localhost
    _HAVE_REQUESTS = True
except Exception:  # requests not installed yet
    _HAVE_REQUESTS = False

BASE = os.environ.get("IBKR_BASE_URL", "https://localhost:5000/v1/api")
_TIMEOUT = 15


def _session():
    s = requests.Session()
    s.verify = False  # local gateway uses a self-signed certificate
    return s


def available() -> bool:
    """True only if the gateway is reachable AND the session is authenticated."""
    if not _HAVE_REQUESTS:
        return False
    try:
        r = _session().get(f"{BASE}/iserver/auth/status", timeout=_TIMEOUT)
        return bool(r.ok and r.json().get("authenticated"))
    except Exception:
        return False


def _conid(session, symbol: str) -> str:
    """Resolve a stock symbol to an IBKR contract id (conid)."""
    r = session.post(
        f"{BASE}/iserver/secdef/search",
        json={"symbol": symbol, "name": False, "secType": "STK"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    for row in r.json() or []:
        if row.get("conid"):
            return str(row["conid"])
    raise ValueError(f"IBKR: no stock contract found for '{symbol}'.")


def get_history(symbol: str, period: str = "2y", bar: str = "1d") -> pd.DataFrame:
    """Daily history as a DataFrame (DatetimeIndex + Open/High/Low/Close/Volume),
    matching the shape market_data feeds into the indicator pipeline."""
    s = _session()
    conid = _conid(s, symbol)
    r = s.get(
        f"{BASE}/iserver/marketdata/history",
        params={"conid": conid, "period": period, "bar": bar, "outsideRth": "false"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    bars = (r.json() or {}).get("data") or []
    if not bars:
        raise ValueError(f"IBKR: no history returned for '{symbol}'.")
    raw = pd.DataFrame(bars)
    raw["date"] = pd.to_datetime(raw["t"], unit="ms")  # IBKR timestamps are epoch ms
    raw = raw.set_index("date")
    out = pd.DataFrame(
        {
            "Open": raw.get("o"),
            "High": raw.get("h"),
            "Low": raw.get("l"),
            "Close": raw["c"],
            "Volume": raw.get("v", 0),
        }
    )
    return out.dropna(subset=["Close"])


def snapshot_price(symbol: str):
    """Best-effort last price via the snapshot endpoint (field 31 = Last).
    Returns None on any failure so callers can fall back to another source."""
    if not _HAVE_REQUESTS:
        return None
    try:
        s = _session()
        conid = _conid(s, symbol)
        # The snapshot endpoint often needs one priming call before data lands.
        for _ in range(2):
            r = s.get(
                f"{BASE}/iserver/marketdata/snapshot",
                params={"conids": conid, "fields": "31"},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json() or []
            if data and data[0].get("31"):
                # field 31 can be prefixed (e.g. "C123.45" when halted/closed)
                return float(str(data[0]["31"]).lstrip("CcHh").replace(",", ""))
    except Exception:
        return None
    return None
