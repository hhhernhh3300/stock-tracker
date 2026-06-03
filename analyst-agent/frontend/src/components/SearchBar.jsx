import { useState } from 'react'

export default function SearchBar({ onSubmit, loading }) {
  const [value, setValue] = useState('')
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit(value)
      }}
      className="flex gap-2"
    >
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Enter a ticker (e.g. AAPL, MSFT, NVDA)"
        aria-label="Ticker symbol"
        className="flex-1 rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-slate-900 outline-none focus:border-clay focus:ring-2 focus:ring-clay/20"
        autoFocus
      />
      <button
        type="submit"
        disabled={loading}
        className="rounded-lg bg-clay px-5 py-2.5 font-medium text-white transition hover:bg-clay-dark disabled:opacity-50"
      >
        {loading ? 'Analyzing…' : 'Analyze'}
      </button>
    </form>
  )
}
