# 📈 Analyst Agent

An educational "financial analyst agent." Enter a ticker → the backend pulls market
data, computes technical indicators (50/200-day SMA, RSI, MACD), aggregates fundamentals
and Wall-Street analyst consensus, and asks **Claude** to produce a structured
**buy / sell / hold** read for the upcoming trading days, with reasoning and a risk level.
A React dashboard shows the AI summary plus a price chart with moving averages and an RSI panel.

> ⚠️ **Educational and research use only.** This is **not** financial advice, **not** a
> personalized recommendation, and **not** the output of a licensed financial advisor.
> The disclaimer is enforced in the AI prompt and shown throughout the UI.

---

## Architecture

```
analyst-agent/
├── backend/                 FastAPI + Python
│   ├── app/
│   │   ├── main.py          API endpoints (/api/analyze/{ticker}, /api/health)
│   │   ├── market_data.py   Yahoo Finance snapshot (+ optional Alpha Vantage)
│   │   ├── indicators.py    SMA, RSI (Wilder), MACD
│   │   └── analyst.py       Claude prompt module (structured buy/sell/hold)
│   ├── requirements.txt
│   └── .env.example
└── frontend/                React + Vite + Tailwind + Recharts
    ├── src/
    │   ├── App.jsx
    │   ├── api.js
    │   └── components/      SearchBar, SummaryPanel, PriceChart, RsiChart, StatsGrid
    └── package.json
```

**Flow:** `frontend → GET /api/analyze/{ticker} → market_data (yfinance) → indicators →
analyst (Claude API) → JSON → dashboard`.

### A note on data sources (read this)

The brief asked for many sources (Webull, IBKR, moomoo, Tiger, Robinhood, Seeking Alpha,
Investing.com, TradingView, MarketWatch, Bloomberg, Reuters, WSJ, …). In reality:

- **Brokerages** (Webull/IBKR/moomoo/Tiger/Robinhood) require an authenticated account.
- **Bloomberg / WSJ / Reuters / Seeking Alpha** are paywalled, and scraping them violates
  their terms.

So this app standardizes on **Yahoo Finance** (free, broad coverage), which **already
aggregates Wall-Street analyst ratings and price targets** from many brokerages — that's the
"analyst consensus" you see. `market_data.py` includes an **Alpha Vantage** quote fallback and
is structured with an adapter pattern so you can plug in a paid provider later.

---

## Prerequisites

- **Python 3.10+** (the backend uses pandas / yfinance / the Anthropic SDK)
- **Node.js 18+** (for the React frontend)
- An **Anthropic API key** → https://console.anthropic.com/settings/keys

---

## 1) Backend — FastAPI

```bash
cd analyst-agent/backend

# create + activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
# source venv/bin/activate

pip install -r requirements.txt

# configure your key
copy .env.example .env        # Windows  (macOS/Linux: cp .env.example .env)
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# run the API (http://localhost:8000)
uvicorn app.main:app --reload --port 8000
```

Quick check: open http://localhost:8000/api/health → `{"status":"ok","ai_configured":true}`.
Try http://localhost:8000/api/analyze/AAPL to see the raw JSON.

> If `pip install` complains about the `anthropic` version, run `pip install -U anthropic`
> (structured outputs need a recent SDK).

## 2) Frontend — React + Tailwind

In a **second terminal**:

```bash
cd analyst-agent/frontend
npm install
npm run dev
```

Open **http://localhost:5173**, type a ticker (e.g. `AAPL`, `MSFT`, `NVDA`), and hit Analyze.
The frontend talks to `http://localhost:8000` by default; override with
`frontend/.env` → `VITE_API_URL=...` if your backend runs elsewhere.

---

## How the AI call works (`analyst.py`)

- Model: **`claude-opus-4-8`** with **adaptive thinking**.
- **Structured outputs** — the response is validated against the `StockAssessment` schema
  (`signal`, `conviction`, `risk_level`, `time_horizon`, `summary`, `bullish_factors`,
  `bearish_factors`, `technical_read`, `reasoning`, `disclaimer`), so the frontend always gets
  clean, typed JSON.
- **Prompt caching** on the static system prompt → cheaper/faster repeat calls.
- The system prompt hard-codes the compliance rule: educational only, not advice, never a
  buy/sell instruction.

If the AI call fails (e.g. missing key, rate limit), the API still returns the market data and
charts and surfaces the error in `ai_error` — the dashboard degrades gracefully.

---

## Extending it

- **Add a data provider:** implement a `get_*` function in `market_data.py` and merge its fields
  into `get_market_snapshot`, mirroring `_alpha_vantage_quote`.
- **More indicators:** add to `indicators.py` (e.g. Bollinger Bands, ATR) and include them in
  `_indicator_frame` / `_derive_latest`.
- **Deploy:** containerize the backend (uvicorn/gunicorn), `npm run build` the frontend to static
  files, and restrict `CORS_ORIGINS` (env var) to your frontend's origin.

---

## Using IBKR as a data source (optional)

IBKR has a vast equity universe and solid research coverage, but **no simple
API-key REST endpoint** — you must run IBKR's **Client Portal Gateway** locally
and stay logged in. Once it's up, this app can pull **price history + the live
quote** from IBKR (see `app/ibkr.py`).

1. Download the **Client Portal Gateway** (Web API) from IBKR and unzip it.
2. Start it (Windows): `bin\run.bat root\conf.yaml`. It listens on `https://localhost:5000`.
3. Open **https://localhost:5000** in your browser and log in with your IBKR
   credentials + 2FA. Keep the gateway running; sessions time out after inactivity —
   re-login when prompted.
4. In `backend/.env` set `DATA_SOURCE=ibkr` (force IBKR) or `DATA_SOURCE=auto`
   (IBKR when authenticated, else Yahoo). Optionally set `IBKR_BASE_URL`.
5. Restart `uvicorn`. The dashboard's "Data" line will now read *"IBKR Client
   Portal — prices/history; Yahoo — fundamentals & analyst consensus."*

**Notes.** The gateway uses a self-signed localhost cert (the adapter sets
`verify=False`). Real-time quotes require the relevant IBKR **market-data
subscriptions**; delayed/last prices and historical bars are broadly available.
IBKR's deep fundamentals/analyst research is subscription-gated and inconsistent
over the API, so fundamentals + consensus stay on Yahoo. In `auto` mode the app
falls back to Yahoo automatically whenever the gateway isn't authenticated.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `404 No price history` | Bad/illiquid symbol. Try a major US ticker. |
| `ai_error: ANTHROPIC_API_KEY is not set` | Put the key in `backend/.env`, restart uvicorn. |
| Charts empty / CORS error in console | Backend not running, or `VITE_API_URL` points to the wrong host. |
| `messages.parse` AttributeError | `pip install -U anthropic`. |
| yfinance returns sparse fundamentals | Yahoo fields are best-effort; missing values show as "n/a". |

---

*Not affiliated with any broker or data provider. For educational and research purposes only.*
