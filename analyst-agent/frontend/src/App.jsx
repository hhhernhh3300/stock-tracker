import { useState } from 'react'
import { analyzeTicker } from './api'
import SearchBar from './components/SearchBar'
import SummaryPanel from './components/SummaryPanel'
import PriceChart from './components/PriceChart'
import RsiChart from './components/RsiChart'
import StatsGrid from './components/StatsGrid'

export default function App() {
  const [ticker, setTicker] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function run(symbol) {
    const s = (symbol || '').trim().toUpperCase()
    if (!s) return
    setLoading(true)
    setError(null)
    try {
      const result = await analyzeTicker(s)
      setData(result)
      setTicker(s)
    } catch (e) {
      setError(e.message)
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-4">
          <div>
            <h1 className="text-xl font-bold text-slate-900">📈 Analyst Agent</h1>
            <p className="text-xs text-slate-500">
              Educational technical + fundamental + analyst-consensus read
            </p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-4 py-6">
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <strong>Educational &amp; research use only.</strong> Not financial advice, not a
          personalized recommendation, and not the output of a licensed advisor. Always do your own
          research before making any investment decision.
        </div>

        <SearchBar onSubmit={run} loading={loading} />

        {error && (
          <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-800">
            {error}
          </div>
        )}

        {loading && (
          <div className="py-12 text-center text-slate-500">Analyzing {ticker || '…'}…</div>
        )}

        {data && !loading && (
          <div className="space-y-6">
            <MetaHeader data={data} />
            <SummaryPanel
              assessment={data.assessment}
              aiError={data.ai_error}
            />
            <PriceChart series={data.series} currency={data.meta.currency} />
            <RsiChart series={data.series} />
            <StatsGrid data={data} />
            <p className="pt-2 text-xs text-slate-400">
              {data.disclaimer} · Data: {data.data_source} · {data.as_of}
            </p>
          </div>
        )}

        {!data && !loading && !error && (
          <div className="py-16 text-center text-slate-400">
            Enter a ticker symbol above to begin (e.g. AAPL, MSFT, NVDA).
          </div>
        )}
      </main>
    </div>
  )
}

function MetaHeader({ data }) {
  const { meta, quote } = data
  const chg = quote.day_change_pct
  const chgColor =
    chg == null ? 'text-slate-400' : chg >= 0 ? 'text-emerald-600' : 'text-rose-600'
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <div>
        <div className="text-2xl font-bold text-slate-900">{meta.ticker}</div>
        <div className="text-sm text-slate-500">
          {meta.name}
          {meta.sector ? ` · ${meta.sector}` : ''}
        </div>
      </div>
      <div className="text-right">
        <div className="text-2xl font-semibold text-slate-900">
          {quote.price != null ? `${quote.price.toLocaleString()} ${meta.currency}` : '—'}
        </div>
        <div className={`text-sm font-medium ${chgColor}`}>
          {chg == null ? '' : `${chg >= 0 ? '+' : ''}${chg}% today`}
        </div>
      </div>
    </div>
  )
}
