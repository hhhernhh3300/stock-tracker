"""Technical-indicator calculations.

Pure pandas/numpy — no network calls. Each function takes a `close` price
Series (indexed by date) and returns a Series aligned to the same index, so the
results line up with the price chart on the frontend.
"""
from __future__ import annotations

import pandas as pd


def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple moving average over `window` periods."""
    return close.rolling(window=window, min_periods=window).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing (the standard 14-period RSI).

    Returns values in [0, 100]. >70 is commonly read as overbought, <30 oversold.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder's smoothing is an EMA with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # When there are no losses over the window, RSI is defined as 100.
    out = out.where(avg_loss != 0, 100.0)
    return out


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Moving Average Convergence Divergence.

    Returns (macd_line, signal_line, histogram).
      macd_line  = EMA(fast) - EMA(slow)
      signal     = EMA(signal) of macd_line
      histogram  = macd_line - signal
    A macd_line crossing above the signal line is a bullish momentum signal.
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram
