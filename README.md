# stock-tracker

Daily rules-based **breakout screener**. Refreshes every weekday morning via GitHub Actions and publishes a public dashboard on GitHub Pages.

**Live site:** https://hhhernhh3300.github.io/stock-tracker/

## What it shows

Three sections, each a starting list for further research:

1. **52-week high breakouts** — stocks within 2% of their 52-week high with positive trend.
2. **Volume surge** — today's volume ≥ 2× the 20-day average. Often an early signal of institutional interest or news.
3. **Top relative strength** — best 3-month return vs SPY. Leaders tend to keep leading — until the regime turns.

## What it does NOT do

It does not predict tomorrow's gainers. Nobody can. These are well-known technical patterns that *sometimes* precede further upside — and often don't. Treat the output as a watchlist, not a buy list.

## How it works

```
GitHub Actions cron (weekdays 11:00 UTC)
    └── scripts/screen.py runs
           ├── pulls 1y of OHLCV via yfinance for each ticker in data/universe.txt
           ├── computes signals (breakouts, volume surge, RS vs SPY)
           └── writes data/screener.json
    └── commits data/screener.json
GitHub Pages serves index.html, which fetches data/screener.json
```

## Customize

- Edit `data/universe.txt` to change which stocks are scanned.
- Edit thresholds in `scripts/screen.py` (`NEAR_HIGH_PCT`, `VOL_SURGE_MULT`, etc.).
- The workflow runs on push as well, so a config change triggers a fresh scan.

## Manual run

```bash
pip install -r scripts/requirements.txt
python scripts/screen.py
```

Writes `data/screener.json` locally — open `index.html` in a browser to view.

## Disclaimer

Not financial advice. Data may be delayed. Past performance is not indicative of future results.
