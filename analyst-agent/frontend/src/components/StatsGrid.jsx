function fmt(v) {
  if (v == null) return '—'
  return typeof v === 'number' ? v.toLocaleString() : v
}

export default function StatsGrid({ data }) {
  const { indicators: i, fundamentals: f, analyst: a, quote: q } = data

  const cells = [
    ['RSI (14)', fmt(i.rsi), i.rsi_zone],
    ['Trend', i.trend ? (i.trend.startsWith('golden') ? 'Golden cross' : 'Death cross') : '—', null],
    ['MACD', i.macd_state ? (i.macd_state.startsWith('bullish') ? 'Bullish' : 'Bearish') : '—', null],
    ['SMA 50 / 200', `${fmt(i.sma50)} / ${fmt(i.sma200)}`, null],
    ['Trailing P/E', fmt(f.trailing_pe), null],
    ['Forward P/E', fmt(f.forward_pe), null],
    ['Beta', fmt(f.beta), null],
    ['Analyst rec', a.recommendation || '—', a.num_analysts ? `${a.num_analysts} analysts` : null],
    [
      'Mean target',
      fmt(a.target_mean),
      a.target_upside_pct != null
        ? `${a.target_upside_pct >= 0 ? '+' : ''}${a.target_upside_pct}% vs price`
        : null,
    ],
    ['52-wk range', `${fmt(q.fifty_two_week_low)}–${fmt(q.fifty_two_week_high)}`, null],
  ]

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="mb-3 font-semibold text-slate-900">Key metrics</h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {cells.map(([label, value, sub]) => (
          <div key={label} className="rounded-lg bg-slate-50 px-3 py-2">
            <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
            <div className="text-sm font-semibold text-slate-800">{value}</div>
            {sub && <div className="text-[11px] capitalize text-slate-500">{sub}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
