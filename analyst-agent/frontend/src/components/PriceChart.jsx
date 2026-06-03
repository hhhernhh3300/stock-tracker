import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from 'recharts'

export default function PriceChart({ series }) {
  const dates = series?.date || []
  const data = dates.map((d, idx) => ({
    date: d,
    close: series.close[idx],
    sma50: series.sma50[idx],
    sma200: series.sma200[idx],
  }))
  const tickEvery = Math.max(1, Math.ceil(data.length / 6))

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="mb-2 font-semibold text-slate-900">Price &amp; Moving Averages</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} interval={tickEvery} minTickGap={20} />
          <YAxis tick={{ fontSize: 11 }} domain={['auto', 'auto']} width={55} />
          <Tooltip formatter={(v) => (v == null ? '—' : Number(v).toFixed(2))} />
          <Legend />
          <Line type="monotone" dataKey="close" name="Close" stroke="#0f172a" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="sma50" name="SMA 50" stroke="#C15F3C" dot={false} strokeWidth={1.5} />
          <Line type="monotone" dataKey="sma200" name="SMA 200" stroke="#2563eb" dot={false} strokeWidth={1.5} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
