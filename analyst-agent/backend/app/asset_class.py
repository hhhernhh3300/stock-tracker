"""Asset-class detection.

Maps a Yahoo Finance symbol to one of our asset classes so the rest of the app
(snapshot context, KPI panel, LLM prompt) can branch on it. Equity metrics like
P/E or analyst price targets are meaningless for a gold future or an FX pair, so
knowing the class lets us show the *native* scorecard for each instead.

Primary signal: Yahoo's ``instrumentType`` from the /v8/chart meta block
(EQUITY / FUTURE / CURRENCY / CRYPTOCURRENCY / INDEX / ETF), which the snapshot
already fetches. Falls back to the symbol's suffix/prefix convention when the
chart meta doesn't carry a type.
"""
from __future__ import annotations

# Yahoo instrumentType -> our asset class
_BY_TYPE = {
    "FUTURE": "commodity",
    "CURRENCY": "fx",
    "CRYPTOCURRENCY": "crypto",
    "INDEX": "index",
    "ETF": "fund",
    "MUTUALFUND": "fund",
    "EQUITY": "equity",
}


def classify(instrument_type: str | None, symbol: str) -> str:
    """Return one of: equity, commodity, fx, crypto, index, fund.

    Prefers Yahoo's instrumentType; falls back to the symbol convention:
      ``=F`` -> commodity future · ``=X`` -> FX · ``-USD/-USDT/-USDC`` -> crypto
      ``^``  -> index · anything else -> equity.
    """
    it = (instrument_type or "").upper()
    if it in _BY_TYPE:
        return _BY_TYPE[it]

    s = (symbol or "").upper()
    if s.endswith("=F"):
        return "commodity"
    if s.endswith("=X"):
        return "fx"
    if s.endswith(("-USD", "-USDT", "-USDC")):
        return "crypto"
    if s.startswith("^"):
        return "index"
    return "equity"
