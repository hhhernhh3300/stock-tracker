// API base URL resolution:
//   - If VITE_API_URL is set at build time, use it (separate-backend deploys).
//   - Otherwise default to same-origin ('') so the app calls /api/... on the
//     current host. This is what the single-service Docker deploy uses (FastAPI
//     serves this frontend), and it also works in local dev via Vite's proxy.
const BASE = import.meta.env.VITE_API_URL ?? ''

export async function analyzeTicker(ticker) {
  const res = await fetch(`${BASE}/api/analyze/${encodeURIComponent(ticker)}`)
  if (!res.ok) {
    let detail = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body && body.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.json()
}
