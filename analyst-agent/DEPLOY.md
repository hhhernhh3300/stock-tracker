# 🚀 Deploy the Analyst Agent to a public website

Locally the app runs at `http://localhost:5173` (frontend) + `http://localhost:8000`
(backend). To give it a **public URL** you host it on a server you own an account with.

> You deploy with **your own** hosting account — that one button click happens inside
> your account, so I can't press it for you, but every file you need is in this repo.
> The app works with **no LLM key** (free rule-based engine); add a key later in the
> host's dashboard to enable AI.

**Pick one:**
- **Option A (recommended) — one URL, one service** via the [`Dockerfile`](Dockerfile).
  Simplest path to a single live link; no CORS to configure.
- **Option B — two services** (separate frontend + backend) on Vercel/Netlify + Render.
- **Option C — Cloudflare** Pages (frontend) + a Python host for the backend.

---

## Option A — Render, single URL via Docker (recommended, ~5 minutes)

This is the easiest way to get **one** public link. The [`Dockerfile`](Dockerfile) builds
the React frontend and runs FastAPI, which **serves that frontend itself** — so the whole
app is one service at one URL, with no CORS to wire up. The included
[`render.yaml`](render.yaml) is preconfigured for this.

1. **Push the repo to GitHub** (already done).
2. Create a free account at **https://render.com** and connect your GitHub.
3. Dashboard → **New +** → **Blueprint** → select `hhhernhh3300/stock-tracker`.
   - If prompted for the Blueprint location, point it at `analyst-agent/render.yaml`.
4. Click **Apply**. Render builds the Docker image (first build ~3–5 min) and assigns
   **one** URL, e.g. `https://analyst-agent.onrender.com` ← **this is your website**.
5. Open that URL and analyze a ticker. ✅ (No `VITE_API_URL`/`CORS_ORIGINS` wiring needed.)

**Enable AI (optional, free):** open the service → *Environment*, add **one**:
- `GEMINI_API_KEY` (+ optional `LLM_PROVIDER=gemini`) — free tier, aistudio.google.com
- or `OPENAI_COMPAT_API_KEY` + `OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1`
  + `OPENAI_COMPAT_MODEL=llama-3.3-70b-versatile` + `LLM_PROVIDER=openai_compatible` (Groq, free)

**Prefer Railway/Fly.io?** Any host that builds a Dockerfile works the same way: point it
at this repo (root `analyst-agent`), it reads the `Dockerfile`, and exposes one URL. They
inject `$PORT`, which the container already honors.

**Test the image locally first (optional):**
```bash
cd analyst-agent
docker build -t analyst-agent .
docker run --rm -p 8000:8000 analyst-agent
# open http://localhost:8000
```

> ⏱️ Free Render instances **sleep after ~15 min idle**; the first request after that
> cold-starts (~30–60s). Upgrade to a paid instance to keep it warm.

---

## Option B — Vercel/Netlify (frontend) + Render (backend)

Use this if you prefer Vercel or Netlify for the static site and a separate backend.

**Backend on Render:**
1. New + → **Web Service** → repo → root `analyst-agent/backend`.
2. Build: `pip install -r requirements.txt`
   Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Health check path: `/api/health`. Note the URL it assigns.
4. Add `CORS_ORIGINS=<your frontend URL>` (set after step below) + any LLM key.

**Frontend on Vercel:**
1. Import the repo at https://vercel.com/new.
2. **Root Directory:** `analyst-agent/frontend`
   Framework preset: **Vite** · Build: `npm run build` · Output: `dist`
3. Environment Variable: `VITE_API_URL = https://<your-render-backend>.onrender.com`
4. Deploy → you get `https://<project>.vercel.app`. Put that URL into the backend's
   `CORS_ORIGINS` and redeploy the backend.

**Frontend on Netlify** is the same idea: base directory `analyst-agent/frontend`,
build `npm run build`, publish `dist`, env `VITE_API_URL=...`.

---

## Option C — Cloudflare Pages (frontend) + Python backend elsewhere

Cloudflare is excellent for the **frontend** (Pages = free global CDN). Important: it
has **no traditional Python server**, so the FastAPI backend can't run on Cloudflare as
written — keep it on Render/Railway/Fly. The repo includes
[`frontend/wrangler.toml`](frontend/wrangler.toml) and an optional proxy Function at
`frontend/functions/api/[[path]].js`.

**C1 — Pages + same-origin proxy (recommended, no CORS):**
1. Deploy the backend somewhere (e.g. Render, Option A or B) → note its URL, e.g.
   `https://analyst-agent.onrender.com`.
2. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git** → pick
   `hhhernhh3300/stock-tracker`.
   - **Root directory:** `analyst-agent/frontend`
   - **Build command:** `npm run build`
   - **Build output directory:** `dist`
3. **Settings → Environment variables** → add `BACKEND_URL = https://<backend-url>`.
   (Do **not** set `VITE_API_URL` for this option — the app calls relative `/api/...`,
   which the included Pages Function proxies to `BACKEND_URL`. Same origin → no CORS.)
4. Save & deploy. Your website is `https://<project>.pages.dev`. Done. ✅

**C2 — Pages calling the backend directly (no proxy):**
1. Same Pages setup as above, but set `VITE_API_URL = https://<backend-url>` instead of
   `BACKEND_URL`, and delete/ignore the proxy Function.
2. On the backend set `CORS_ORIGINS = https://<project>.pages.dev`.

**CLI alternative (Wrangler):**
```bash
cd analyst-agent/frontend
npm install
npm run build
npx wrangler pages deploy dist           # first run prompts you to log in
# then add BACKEND_URL (C1) or VITE_API_URL (C2) in the Pages project settings
```

> Running the FastAPI app as a **Cloudflare Python Worker** is technically possible
> (Workers Python is in beta) but **not supported here** — it can't use `pandas`/
> `yfinance`, so you'd have to reimplement the data layer with `fetch`. Stick with a
> Python host for the backend.

---

## Other free hosts (single-URL Docker, like Option A)

The [`Dockerfile`](Dockerfile) makes the whole app portable to any Docker-friendly host:
- **Railway** — New Project → Deploy from repo → it detects the Dockerfile → one URL.
- **Fly.io** — `fly launch` in `analyst-agent/` (uses the Dockerfile) → `fly deploy`.
- **Hugging Face Spaces** (Docker SDK) → one always-on URL.
All inject `$PORT`, which the container honors automatically.

---

## Production checklist

- [ ] Single-URL (Option A): nothing to wire — open the URL and go.
- [ ] Two-service (Option B/C2): `CORS_ORIGINS` set to your exact frontend origin (not `*`),
      and `VITE_API_URL` points at the public backend URL (https).
- [ ] LLM keys live ONLY in the host's environment settings — never in git.
- [ ] Confirm `https://<your-url>/api/health` returns `{"status":"ok",...}`.
- [ ] Educational-only disclaimer is visible (it is, by design).
