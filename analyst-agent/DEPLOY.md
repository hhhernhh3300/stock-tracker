# 🚀 Deploy the Analyst Agent to a public website

Locally the app runs at `http://localhost:5173` (frontend) + `http://localhost:8000`
(backend). To give it a **public URL** you must host both parts on a server you own an
account with. This guide uses **Render** (free tier, one blueprint deploys both). A
Netlify/Vercel + Render split is also covered.

> You deploy with **your own** hosting account. No code change is needed — the frontend
> already reads the backend URL from `VITE_API_URL`, and CORS is configurable via
> `CORS_ORIGINS`. The app works with **no LLM key** (free rule-based engine); add a key
> later in the host's dashboard to enable AI.

---

## Option A — Render Blueprint (both services, ~5 minutes)

This repo ships [`render.yaml`](render.yaml), which defines the backend (FastAPI) and the
frontend (static React build).

1. **Push the repo to GitHub** (already done).
2. Create a free account at **https://render.com** and connect your GitHub.
3. Dashboard → **New +** → **Blueprint** → select `hhhernhh3300/stock-tracker`.
   - If your repo has multiple projects, set the Blueprint's root to `analyst-agent`
     (Render looks for `render.yaml`). You can also keep a copy of `render.yaml` at the
     repo root if needed.
4. Render creates two services and gives them URLs, e.g.:
   - Backend  → `https://analyst-agent-api.onrender.com`
   - Frontend → `https://analyst-agent-web.onrender.com`  ← **this is your website**
5. **Wire the two together** (one-time):
   - Open the **web** service → *Environment* → set
     `VITE_API_URL = https://analyst-agent-api.onrender.com` → **Save** (triggers rebuild).
   - Open the **api** service → *Environment* → set
     `CORS_ORIGINS = https://analyst-agent-web.onrender.com` → **Save**.
6. Open the frontend URL and analyze a ticker. ✅

**Enable AI (optional, free):** on the **api** service → *Environment*, add **one**:
- `GEMINI_API_KEY` (+ optional `LLM_PROVIDER=gemini`) — free tier, aistudio.google.com
- or `OPENAI_COMPAT_API_KEY` + `OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1`
  + `OPENAI_COMPAT_MODEL=llama-3.3-70b-versatile` + `LLM_PROVIDER=openai_compatible` (Groq, free)

> ⏱️ Free Render backends **sleep after ~15 min idle**; the first request after that
> cold-starts (~30–60s). Upgrade the api service to a paid instance to keep it warm.

---

## Option B — Vercel/Netlify (frontend) + Render (backend)

Use this if you prefer Vercel or Netlify for the static site.

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

## Other free backend hosts

The backend is a standard FastAPI/uvicorn app, so it also runs on:
- **Railway** — detect Python, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- **Fly.io** — `fly launch` in `backend/` (needs a Dockerfile or the Python buildpack).
- **Hugging Face Spaces** (Docker) / **PythonAnywhere** — for a quick always-on box.

---

## Production checklist

- [ ] `CORS_ORIGINS` set to your exact frontend origin (not `*`).
- [ ] LLM keys live ONLY in the host's environment settings — never in git.
- [ ] `VITE_API_URL` points at the public backend URL (https).
- [ ] Confirm `https://<backend>/api/health` returns `{"status":"ok",...}`.
- [ ] Educational-only disclaimer is visible (it is, by design).
