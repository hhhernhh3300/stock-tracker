import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  CartesianGrid,
} from 'recharts'

export default function RsiChart({ series }) {
  const dates = series?.date || []
  const data = dates.map((d, idx) => ({ date: d, rsi: series.rsi[idx] }))
  const tickEvery = Math.max(1, Math.ceil(data.length / 6))

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="mb-2 font-semibold text-slate-900">RSI (14)</h3>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} interval={tickEvery} minTickGap={20} />
          <YAxis domain={[0, 100]} ticks={[0, 30, 50, 70, 100]} tick={{ fontSize: 11 }} width={55} />
          <Tooltip formatter={(v) => (v == null ? '—' : Number(v).toFixed(1))} />
          <ReferenceLine y={70} stroke="#e11d48" strokeDasharray="4 4" />
          <ReferenceLine y={30} stroke="#059669" strokeDasharray="4 4" />
          <Line type="monotone" dataKey="rsi" name="RSI" stroke="#7c3aed" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
      <p className="mt-1 text-xs text-slate-400">
        Dashed lines mark the conventional overbought (70) and oversold (30) thresholds.
      </p>
    </div>
  )
}
