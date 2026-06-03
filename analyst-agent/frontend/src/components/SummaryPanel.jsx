const SIGNAL = {
  buy: { label: 'BUY', cls: 'bg-emerald-100 text-emerald-800 border-emerald-300' },
  hold: { label: 'HOLD', cls: 'bg-amber-100 text-amber-800 border-amber-300' },
  sell: { label: 'SELL', cls: 'bg-rose-100 text-rose-800 border-rose-300' },
}

const RISK = {
  low: 'text-emerald-700',
  moderate: 'text-amber-700',
  high: 'text-orange-700',
  'very high': 'text-rose-700',
}

export default function SummaryPanel({ assessment, aiError }) {
  if (aiError || !assessment) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="mb-1 font-semibold text-slate-900">AI Assessment</h2>
        <p className="text-sm text-slate-500">
          {aiError || 'No assessment available.'} The market data and charts below are still shown.
        </p>
      </div>
    )
  }

  const sig = SIGNAL[assessment.signal] || SIGNAL.hold

  return (
    <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`rounded-md border px-3 py-1 text-sm font-bold ${sig.cls}`}>
          {sig.label}
        </span>
        <span className="text-sm text-slate-600">
          Conviction: <b className="capitalize">{assessment.conviction}</b>
        </span>
        <span className="text-sm text-slate-600">
          Risk:{' '}
          <b className={`capitalize ${RISK[assessment.risk_level] || ''}`}>
            {assessment.risk_level}
          </b>
        </span>
        <span className="text-sm text-slate-500">· {assessment.time_horizon}</span>
      </div>

      <p className="text-slate-800">{assessment.summary}</p>

      <div className="grid gap-4 sm:grid-cols-2">
        <FactorList title="Bullish factors" items={assessment.bullish_factors} tone="emerald" />
        <FactorList title="Bearish factors" items={assessment.bearish_factors} tone="rose" />
      </div>

      <Section title="Technical read">{assessment.technical_read}</Section>
      <Section title="Reasoning">{assessment.reasoning}</Section>

      <p className="border-t border-slate-100 pt-3 text-xs italic text-slate-400">
        {assessment.disclaimer}
      </p>
    </div>
  )
}

function FactorList({ title, items, tone }) {
  const dot = tone === 'emerald' ? 'text-emerald-500' : 'text-rose-500'
  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-slate-700">{title}</h3>
      <ul className="space-y-1">
        {(items || []).map((t, idx) => (
          <li key={idx} className="flex gap-2 text-sm text-slate-600">
            <span className={dot}>•</span>
            <span>{t}</span>
          </li>
        ))}
        {(!items || items.length === 0) && (
          <li className="text-sm text-slate-400">None noted.</li>
        )}
      </ul>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-slate-700">{title}</h3>
      <p className="text-sm leading-relaxed text-slate-600">{children}</p>
    </div>
  )
}
