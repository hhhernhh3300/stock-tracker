const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

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
