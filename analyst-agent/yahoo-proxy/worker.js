/**
 * Yahoo Finance proxy — Cloudflare Worker.
 *
 * WHY: Yahoo blocks shared data-center IP ranges (like Render's) for its
 * authenticated data endpoints (/v10 quoteSummary, /v7 quote). Cloudflare's
 * edge IPs are NOT blocked, so routing those calls through this Worker lets the
 * analyst-agent backend fetch fundamentals + analyst consensus reliably.
 *
 * WHAT IT DOES: transparently forwards any Yahoo path it receives to
 * query2.finance.yahoo.com, completing Yahoo's cookie + crumb handshake itself
 * (on a non-blocked IP) and appending the crumb to authenticated endpoints.
 * The crumb is cached ~25 min and refreshed automatically on a 401/403.
 *
 * USAGE (from the backend): instead of
 *   https://query2.finance.yahoo.com/v10/finance/quoteSummary/TSLA?modules=...
 * call
 *   https://<this-worker>.workers.dev/v10/finance/quoteSummary/TSLA?modules=...
 *
 * OPTIONAL HARDENING: set a Worker secret/var named PROXY_TOKEN. When set, the
 * Worker requires a matching `?token=...` on each request (the backend appends
 * it automatically when YAHOO_PROXY_TOKEN is configured). Without PROXY_TOKEN
 * the Worker is open — fine for a personal, obscure URL on the free tier.
 */

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0 Safari/537.36';

// Module-scope crumb cache (persists across requests on a warm isolate).
let _cookie = null;
let _crumb = null;
let _ts = 0;

async function handshake() {
  if (_crumb && Date.now() - _ts < 1_500_000) return; // reuse for ~25 min

  // 1) Grab Yahoo's consent cookie.
  let cookie = '';
  try {
    const r = await fetch('https://fc.yahoo.com/', {
      headers: { 'User-Agent': UA },
      redirect: 'manual',
    });
    const sc = r.headers.get('set-cookie');
    if (sc) cookie = sc.split(';')[0];
  } catch (_) {}

  // 2) Get the crumb tied to that cookie.
  for (const host of ['query2', 'query1']) {
    try {
      const r = await fetch(`https://${host}.finance.yahoo.com/v1/test/getcrumb`, {
        headers: { 'User-Agent': UA, ...(cookie ? { Cookie: cookie } : {}) },
      });
      const c = (await r.text()).trim();
      if (c && c.length < 40 && !c.includes('<')) {
        _cookie = cookie;
        _crumb = c;
        _ts = Date.now();
        return;
      }
    } catch (_) {}
  }
}

function yahooHeaders() {
  return {
    'User-Agent': UA,
    Accept: 'application/json',
    ...(_cookie ? { Cookie: _cookie } : {}),
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Health check.
    if (url.pathname === '/' || url.pathname === '/health') {
      return new Response('yahoo-proxy ok', {
        status: 200,
        headers: { 'access-control-allow-origin': '*' },
      });
    }

    // Optional shared-secret gate.
    if (env && env.PROXY_TOKEN) {
      if (url.searchParams.get('token') !== env.PROXY_TOKEN) {
        return new Response('forbidden', { status: 403 });
      }
      url.searchParams.delete('token'); // don't forward it to Yahoo
    }

    await handshake();

    // Forward path + query to Yahoo; add crumb for authenticated endpoints.
    const target = new URL(
      'https://query2.finance.yahoo.com' + url.pathname + url.search
    );
    if (_crumb && !target.searchParams.has('crumb')) {
      target.searchParams.set('crumb', _crumb);
    }

    let resp;
    try {
      resp = await fetch(target.toString(), { headers: yahooHeaders() });
    } catch (_) {
      return new Response(JSON.stringify({ error: 'upstream fetch failed' }), {
        status: 502,
        headers: { 'content-type': 'application/json' },
      });
    }

    // Crumb expired → refresh once and retry.
    if (resp.status === 401 || resp.status === 403) {
      _crumb = null;
      await handshake();
      if (_crumb) {
        target.searchParams.set('crumb', _crumb);
        try {
          resp = await fetch(target.toString(), { headers: yahooHeaders() });
        } catch (_) {}
      }
    }

    const body = await resp.text();
    return new Response(body, {
      status: resp.status,
      headers: {
        'content-type': 'application/json',
        'cache-control': 'no-store',
        'access-control-allow-origin': '*',
      },
    });
  },
};
