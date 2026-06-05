# Yahoo Finance proxy (Cloudflare Worker)

Yahoo blocks shared data-center IP ranges (like Render's) for its authenticated
data endpoints, so the analyst-agent backend can't fetch fundamentals / analyst
consensus directly. This tiny Worker runs on Cloudflare's edge IPs (which Yahoo
does **not** block) and proxies those calls, completing Yahoo's cookie + crumb
handshake itself.

Result: fundamentals work reliably for **every** ticker — US and Southeast Asia.

---

## Deploy (pick ONE method)

### Option A — Cloudflare dashboard (no tools, ~3 min)
1. Go to https://dash.cloudflare.com → **Workers & Pages** → **Create** → **Create Worker**.
2. Name it `yahoo-proxy` → **Deploy** (it deploys a placeholder).
3. Click **Edit code**, delete the placeholder, paste the entire contents of
   [`worker.js`](./worker.js), then **Deploy**.
4. Copy the Worker URL shown at the top, e.g.
   `https://yahoo-proxy.<your-subdomain>.workers.dev`.

### Option B — Wrangler CLI
```bash
cd analyst-agent/yahoo-proxy
npx wrangler deploy
```
Copy the `*.workers.dev` URL it prints.

---

## Verify the Worker works
Open this in your browser (replace the host):
```
https://yahoo-proxy.<your-subdomain>.workers.dev/v10/finance/quoteSummary/AAPL?modules=price
```
You should see JSON with Apple's price/market-cap — **not** a 401.

---

## Point the backend at it
In your Render service → **Environment** → add:

| Key | Value |
|-----|-------|
| `YAHOO_PROXY_BASE` | `https://yahoo-proxy.<your-subdomain>.workers.dev` |

Save → Render redeploys (~3-5 min). Confirm at
`https://analyst-agent-gfsc.onrender.com/api/health` — you should see
`"yahoo_proxy": true`. Search any ticker; fundamentals will populate.

---

## Optional hardening (shared secret)
By default the Worker is open (fine for an obscure personal URL on the free
tier — 100k requests/day). To lock it to your backend only:

1. Set a secret on the Worker:
   ```bash
   npx wrangler secret put PROXY_TOKEN     # enter any random string
   ```
   (or dashboard → the Worker → **Settings → Variables → Add variable**,
   name `PROXY_TOKEN`, mark **Encrypt**.)
2. On Render, add `YAHOO_PROXY_TOKEN` with the same value.

The backend appends the token automatically; requests without it get a 403.
