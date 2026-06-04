// API base URL resolution:
//   - If VITE_API_URL is set at build time, use it (separate-backend deploys).
//   - Otherwise default to same-origin ('') so the app calls /api/... on the
//     current host. This is what the single-service Docker deploy uses (FastAPI
//     serves this frontend), and it also works in local dev via Vite's proxy.
const BASE = import.meta.env.VITE_API_URL ?? ''

async function _readError(res) {
  let detail = `Request failed (${res.status})`
  try {
    const body = await res.json()
    if (body && body.detail) detail = body.detail
  } catch {
    /* non-JSON error body */
  }
  return detail
}

// Cache-busting fetch options. Belt-and-suspenders with the backend's
// `Cache-Control: no-store` header — some mobile webviews ignore response
// headers but still honour the request-level `cache: 'no-store'` directive and
// a unique query string. Together these guarantee every search hits the live
// endpoint and never re-serves a stale snapshot.
const _noCacheInit = {
  cache: 'no-store',
  headers: { 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
}

// Append a unique `_` param so the URL itself differs every call (defeats any
// proxy/webview that keys its cache purely on the URL).
function _bust(url) {
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}_=${Date.now()}`
}

export async function analyzeTicker(ticker) {
  const url = _bust(`${BASE}/api/analyze/${encodeURIComponent(ticker)}`)
  const res = await fetch(url, _noCacheInit)
  if (!res.ok) throw new Error(await _readError(res))
  return res.json()
}

// Live symbol search for the autocomplete dropdown. Returns an array of
// { symbol, name, exchange, type }. Never throws — returns [] on any failure so
// a transient search hiccup never blocks the user from typing/searching.
export async function searchSymbols(query) {
  const q = (query || '').trim()
  if (!q) return []
  try {
    const res = await fetch(`${BASE}/api/search?q=${encodeURIComponent(q)}`)
    if (!res.ok) return []
    const data = await res.json()
    return Array.isArray(data.results) ? data.results : []
  } catch {
    return []
  }
}

// Free-form Q&A about a ticker, grounded in a fresh server-side snapshot.
// `history` is an optional array of { role: 'user'|'ai', text } turns.
export async function chatAboutTicker(ticker, message, history = []) {
  const res = await fetch(_bust(`${BASE}/api/chat`), {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
    body: JSON.stringify({ ticker, message, history }),
  })
  if (!res.ok) throw new Error(await _readError(res))
  return res.json() // { reply, engine, model, ticker }
}
