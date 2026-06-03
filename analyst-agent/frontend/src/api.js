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

export async function analyzeTicker(ticker) {
  const res = await fetch(`${BASE}/api/analyze/${encodeURIComponent(ticker)}`)
  if (!res.ok) throw new Error(await _readError(res))
  return res.json()
}

// Free-form Q&A about a ticker, grounded in a fresh server-side snapshot.
// `history` is an optional array of { role: 'user'|'ai', text } turns.
export async function chatAboutTicker(ticker, message, history = []) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticker, message, history }),
  })
  if (!res.ok) throw new Error(await _readError(res))
  return res.json() // { reply, engine, model, ticker }
}
