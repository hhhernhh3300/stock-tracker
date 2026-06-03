// Cloudflare Pages Function — reverse proxy for /api/*
//
// Forwards any request hitting https://<your-pages-site>/api/... to your FastAPI
// backend (BACKEND_URL). This makes the app SAME-ORIGIN, so:
//   - the frontend can just call relative "/api/analyze/AAPL" (no VITE_API_URL), and
//   - you don't need to configure CORS on the backend.
//
// Setup: in the Cloudflare Pages dashboard (Settings -> Environment variables) set
//   BACKEND_URL = https://analyst-agent-api.onrender.com   (your backend's public URL)
//
// If BACKEND_URL is not set, this returns a clear 500 so misconfig is obvious.
//
// This file lives at functions/api/[[path]].js so it matches every /api/* route.

export async function onRequest(context) {
  const { request, env, params } = context;

  const backend = env.BACKEND_URL;
  if (!backend) {
    return new Response(
      JSON.stringify({
        detail:
          'Proxy misconfigured: set the BACKEND_URL environment variable in the ' +
          'Cloudflare Pages dashboard to your FastAPI backend URL.',
      }),
      { status: 500, headers: { 'content-type': 'application/json' } }
    );
  }

  // params.path is the catch-all segment(s) after /api/
  const tail = Array.isArray(params.path) ? params.path.join('/') : params.path || '';
  const incoming = new URL(request.url);

  // Rebuild target: <backend>/api/<tail><original query string>
  const base = backend.replace(/\/+$/, '');
  const target = `${base}/api/${tail}${incoming.search}`;

  // Forward the request (method, headers, body) to the backend.
  const proxied = new Request(target, {
    method: request.method,
    headers: request.headers,
    body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
    redirect: 'follow',
  });

  const resp = await fetch(proxied);

  // Pass the backend response straight back to the browser (same origin = no CORS).
  return new Response(resp.body, {
    status: resp.status,
    statusText: resp.statusText,
    headers: resp.headers,
  });
}
