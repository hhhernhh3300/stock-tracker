# 📈 Analyst Agent

An educational "financial analyst agent." Enter a ticker → the backend pulls market
data, computes technical indicators (50/200-day SMA, RSI, MACD), aggregates fundamentals
and Wall-Street analyst consensus, and produces a structured **buy / sell / hold** read
for the upcoming trading days, with reasoning and a risk level.
A React dashboard shows the summary plus a price chart with moving averages and an RSI panel.

The assessment can be generated two ways:

- **Free, no key, default** — a transparent, deterministic rule-based engine
  ([`rules.py`](backend/app/rules.py)) scores the indicators/fundamentals/consensus locally.
- **AI-written (optional)** — bring **your own** LLM API key and the app uses an LLM for
  richer natural-language reasoning. It is **provider-agnostic**: Anthropic, OpenAI,
  Google Gemini, or any OpenAI-compatible gateway (Groq, OpenRouter, Mistral, Cerebras,
  LiteLLM, corporate gateways). Several of these have a **free tier**.

> 🔑 **You supply your own key.** Put it in `backend/.env` (git-ignored). Never commit a
> key or paste it anywhere public — providers auto-revoke leaked keys.

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

- **Python 3.10+** (the backend uses pandas / yfinance)
- **Node.js 18+** (for the React frontend)
- **(Optional) an LLM API key** — only if you want AI-written analysis. Without one,
  the app uses the free rule-based engine. Pick whichever you like:
  | Provider | Get a key | Cost |
  |---|---|---|
  | **Google Gemini** | https://aistudio.google.com/app/apikey | **Free tier** |
  | **Groq** (OpenAI-compatible) | https://console.groq.com/keys | **Free tier** |
  | **OpenRouter** (OpenAI-compatible) | https://openrouter.ai/keys | **Free** `:free` models |
  | OpenAI | https://platform.openai.com/api-keys | Paid (pennies/call) |
  | Anthropic | https://console.anthropic.com/settings/keys | Paid (pennies/call) |

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

# configure (optional — leave keys blank to use the FREE rule-based engine)
copy .env.example .env        # Windows  (macOS/Linux: cp .env.example .env)
# then edit .env. Pick ONE of:
#   FREE, no AI        -> leave all keys blank
#   FREE Gemini AI     -> LLM_PROVIDER=gemini  + GEMINI_API_KEY=...
#   FREE Groq AI       -> LLM_PROVIDER=openai_compatible
#                         OPENAI_COMPAT_API_KEY=...  (from console.groq.com/keys)
#                         OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
#                         OPENAI_COMPAT_MODEL=llama-3.3-70b-versatile
#   Paid OpenAI/Claude -> OPENAI_API_KEY=... / ANTHROPIC_API_KEY=...

# run the API (http://localhost:8000)
uvicorn app.main:app --reload --port 8000
```

Quick check: open http://localhost:8000/api/health → e.g.
`{"status":"ok","ai_configured":true,"llm_provider":"gemini","llm_model":"gemini-1.5-flash","rules_fallback":true}`.
If no key is set you'll see `"ai_configured":false` and the app uses the rule-based engine.
Try http://localhost:8000/api/analyze/AAPL to see the raw JSON.

> The provider SDKs are imported lazily — you only need the package for the provider you
> chose. `pip install -r requirements.txt` installs all of them so any provider works.

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

## 3) Deploy to a public website (not localhost)

Want a shareable URL instead of localhost? See **[DEPLOY.md](DEPLOY.md)**. The repo
includes a Render blueprint ([`render.yaml`](render.yaml)) that hosts the FastAPI backend
and the static React frontend on a **free tier** — you get URLs like
`https://analyst-agent-web.onrender.com`. (You deploy with your own hosting account;
set `VITE_API_URL` to the backend URL and `CORS_ORIGINS` to the frontend URL.)

---

## How the assessment works (`analyst.py` + `rules.py`)

**Engine selection** (in `main.py`):

1. If an LLM key is configured, the LLM writes the assessment.
2. Otherwise (or if the LLM call fails and `ALLOW_RULES_FALLBACK=true`, the default),
   the free deterministic engine in [`rules.py`](backend/app/rules.py) produces it.

The returned `assessment` carries an `engine` field (`anthropic` / `openai` / `gemini` /
`openai_compatible` / `rules`) so you always know which produced it.

**Provider-agnostic LLM layer** (`analyst.py`):

- One `analyze(snap)` entrypoint dispatches to the provider chosen by `LLM_PROVIDER`
  (or auto-detected from whichever key is present).
- Supported: **Anthropic**, **OpenAI**, **Google Gemini**, and **any OpenAI-compatible
  gateway** (Groq, OpenRouter, Mistral, Cerebras, LiteLLM, corporate gateways) via
  `OPENAI_COMPAT_BASE_URL`.
- The model is asked for **JSON only** and the response is validated against the
  `StockAssessment` Pydantic schema (`signal`, `conviction`, `risk_level`, `time_horizon`,
  `summary`, `bullish_factors`, `bearish_factors`, `technical_read`, `reasoning`,
  `disclaimer`), so the frontend always gets clean, typed JSON.
- The system prompt hard-codes the compliance rule: educational only, not advice, never a
  buy/sell instruction.
- SDKs are imported lazily and keys are read **only** from the environment — nothing is
  hardcoded, and `.env` is git-ignored.

If the AI call fails (missing key, rate limit, bad model id), the API still returns the
market data and charts; it falls back to the rule-based engine and surfaces the cause in
`ai_error` — the dashboard degrades gracefully.

### Bringing your own key (security)

- Put the key in `backend/.env` only. **Never** commit it or paste it into chats, issues,
  or screenshots — providers scan public sources and auto-revoke leaked keys within minutes.
- If a key is ever exposed, **rotate it** in the provider console immediately.
- Only use a gateway key (e.g. a corporate/LiteLLM gateway) if you are **authorized** to
  use it for this purpose.

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
