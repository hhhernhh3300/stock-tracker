import { useState, useRef, useEffect } from 'react'
import {
  AreaChart, Area, ComposedChart, Bar, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts'
import { analyzeTicker, chatAboutTicker } from './api'

/* ───────────── design tokens ───────────── */
const T = {
  bg: '#03040a', nav: '#07090f', surface: '#0d1018', surfaceRaised: '#121620',
  border: 'rgba(255,255,255,0.055)', borderMid: 'rgba(255,255,255,0.11)',
  amber: '#e6a72a', amberDim: 'rgba(230,167,42,0.1)', amberBorder: 'rgba(230,167,42,0.28)',
  green: '#2dd4a0', red: '#f16a6a', cyan: '#5bbfed', purple: '#c084fc',
  orange: '#f97316', blue: '#60a5fa',
  text: '#cdd3df', muted: '#5e6573', dim: '#2e333e',
  mono: "var(--font-mono, 'IBM Plex Mono', 'Courier New', monospace)",
  head: "var(--font-sans, 'Plus Jakarta Sans', sans-serif)",
}

const SMA_META = {
  sma20: { label: 'SMA 20', color: T.blue },
  sma50: { label: 'SMA 50', color: T.amber },
  sma100: { label: 'SMA 100', color: T.purple },
  sma200: { label: 'SMA 200', color: T.orange },
}

/* ───────────── formatting helpers ───────────── */
const num = v => (v == null || Number.isNaN(v)) ? null : Number(v)
const fmt = (v, d = 2) => { const n = num(v); return n == null ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: d }) }
const fmtPct = (v, d = 2) => { const n = num(v); return n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(d)}%` }
// fraction (0.55) -> "55.0%"; already-percent values pass through if >1.5
const fmtFrac = v => { const n = num(v); if (n == null) return '—'; const p = Math.abs(n) < 1.5 ? n * 100 : n; return `${p.toFixed(1)}%` }
const fmtMult = v => { const n = num(v); return n == null ? '—' : `${n.toFixed(1)}×` }
const fmtCap = v => {
  const n = num(v); if (n == null) return '—'
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`
  return `$${n.toLocaleString()}`
}
const fmtMoney = (v, cur = '') => { const n = num(v); return n == null ? '—' : `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}${cur ? ' ' + cur : ''}` }

/* ───────────── client-side indicator derivation ───────────── */
function sma(arr, p) {
  const out = Array(arr.length).fill(null)
  for (let i = p - 1; i < arr.length; i++) {
    let s = 0, ok = true
    for (let j = i - p + 1; j <= i; j++) { if (arr[j] == null) { ok = false; break } s += arr[j] }
    if (ok) out[i] = +(s / p).toFixed(2)
  }
  return out
}
function bollinger(arr, p = 20, m = 2) {
  const up = Array(arr.length).fill(null), lo = Array(arr.length).fill(null)
  for (let i = p - 1; i < arr.length; i++) {
    const sl = arr.slice(i - p + 1, i + 1)
    if (sl.some(x => x == null)) continue
    const mean = sl.reduce((a, b) => a + b, 0) / p
    const std = Math.sqrt(sl.reduce((a, b) => a + (b - mean) ** 2, 0) / p)
    up[i] = +(mean + m * std).toFixed(2)
    lo[i] = +(mean - m * std).toFixed(2)
  }
  return { up, lo }
}
function buildSeries(raw) {
  const dates = raw.date || []
  const close = (raw.close || []).map(num)
  const s20 = sma(close, 20), s100 = sma(close, 100)
  const { up, lo } = bollinger(close, 20, 2)
  return dates.map((d, i) => ({
    date: d,
    close: close[i],
    sma20: s20[i], sma50: num(raw.sma50?.[i]), sma100: s100[i], sma200: num(raw.sma200?.[i]),
    bbUpper: up[i], bbLower: lo[i],
    rsi: num(raw.rsi?.[i]),
    macd: num(raw.macd?.[i]), macdSignal: num(raw.macd_signal?.[i]), macdHist: num(raw.macd_hist?.[i]),
    volume: num(raw.volume?.[i]),
  }))
}

/* ───────────── small UI atoms ───────────── */
const SIG = {
  buy: T.green, strong_buy: T.green, hold: T.muted, sell: T.red, strong_sell: T.red,
  low: T.green, moderate: '#f0954c', medium: '#f0954c', high: T.red, 'very high': T.red,
  POS: T.green, NEG: T.red, NEU: T.muted,
}
const sc = s => SIG[(s || '').toLowerCase?.() ?? s] || SIG[s] || T.muted

const Badge = ({ s, label }) => {
  const c = sc(s)
  return (
    <span style={{
      fontSize: 10, fontFamily: T.mono, fontWeight: 700, color: c, background: c + '1a',
      border: `1px solid ${c}44`, padding: '1px 6px', borderRadius: 3, letterSpacing: '0.04em',
      whiteSpace: 'nowrap', textTransform: 'uppercase',
    }}>{label || s}</span>
  )
}

const Panel = ({ title, children, style }) => (
  <div style={{ border: `1px solid ${T.border}`, borderRadius: 8, overflow: 'hidden', ...style }}>
    {title && (
      <div style={{ padding: '7px 13px', background: 'rgba(255,255,255,0.022)', borderBottom: `1px solid ${T.border}` }}>
        <span style={{ fontSize: 10, fontFamily: T.head, fontWeight: 700, color: T.amber, letterSpacing: '0.04em', textTransform: 'uppercase' }}>{title}</span>
      </div>
    )}
    {children}
  </div>
)

const Row = ({ m, v, s, note }) => (
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 13px', borderBottom: `1px solid ${T.border}` }}>
    <span style={{ fontSize: 12, color: T.muted, fontFamily: T.mono }}>{m}</span>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {note && <span style={{ fontSize: 11, color: T.dim, fontFamily: T.mono }}>{note}</span>}
      <span style={{ fontSize: 13, color: T.text, fontFamily: T.mono, fontWeight: 700 }}>{v}</span>
      {s && <Badge s={s} />}
    </div>
  </div>
)

const KPI = ({ label, value }) => (
  <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 6, padding: '10px 13px' }}>
    <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, letterSpacing: '0.04em', marginBottom: 5 }}>{label}</div>
    <div style={{ fontSize: 15, color: T.text, fontFamily: T.mono, fontWeight: 700 }}>{value}</div>
  </div>
)

const tip = { background: T.surfaceRaised, border: `1px solid ${T.border}`, borderRadius: 6, fontSize: 11, fontFamily: T.mono, color: T.text }

/* ───────────── indicator assessment card ───────────── */
const AssessCard = ({ label, value, signal, note, assess }) => {
  const c = sc(signal)
  return (
    <div style={{ border: `1px solid ${T.border}`, borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '9px 14px', background: 'rgba(255,255,255,0.022)', borderBottom: `1px solid ${T.border}` }}>
        <span style={{ fontSize: 11, fontFamily: T.mono, fontWeight: 700, color: T.amber,
          letterSpacing: '0.04em', textTransform: 'uppercase' }}>{label}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {note && <span style={{ fontSize: 11, color: T.dim, fontFamily: T.mono }}>{note}</span>}
          <span style={{ fontSize: 17, fontFamily: T.mono, fontWeight: 700, color: T.text }}>{value}</span>
          {signal && <Badge s={signal} />}
        </div>
      </div>
      {assess && (
        <div style={{ padding: '10px 14px', fontSize: 12, lineHeight: 1.78, color: T.muted, fontFamily: T.mono }}>
          {assess}
        </div>
      )}
    </div>
  )
}

function buildIndicatorCards(i, q) {
  const rsi   = i.rsi   != null ? Number(i.rsi)   : null
  const zone  = (i.rsi_zone   || '').toLowerCase()
  const macd  = i.macd  != null ? Number(i.macd)  : null
  const sigV  = i.macd_signal != null ? Number(i.macd_signal) : null
  const state = (i.macd_state || '').toLowerCase()
  const trend = (i.trend || '').toLowerCase()
  const p50   = i.price_vs_sma50_pct  != null ? Number(i.price_vs_sma50_pct)  : null
  const p200  = i.price_vs_sma200_pct != null ? Number(i.price_vs_sma200_pct) : null
  const fmtV  = v => v != null ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—'
  const cards = []

  if (rsi != null) {
    let signal = 'hold', assess = ''
    if (zone === 'overbought') {
      signal = 'sell'
      assess = `RSI at ${rsi.toFixed(1)} is in overbought territory (>70). The stock has strong momentum but may be due for a short-term pullback or consolidation. Watch for a bearish divergence (price rising while RSI falls) or a drop back below 70 as the first caution signal. Overbought readings can persist in strong uptrends.`
    } else if (zone === 'oversold') {
      signal = 'buy'
      assess = `RSI at ${rsi.toFixed(1)} is in oversold territory (<30). Heavy selling has driven the stock to a statistically low-momentum reading — a mean-reversion bounce is possible. Wait for RSI to cross back above 30 as confirmation rather than catching a falling knife.`
    } else if (rsi > 55) {
      signal = 'buy'
      assess = `RSI at ${rsi.toFixed(1)} is in the neutral-to-bullish zone (55–70). Momentum is positive without being stretched — a constructive zone for an ongoing uptrend. Bulls are in control. Watch for RSI to push above 70 (extension risk) or slip back below 50 (momentum loss).`
    } else if (rsi < 45) {
      signal = 'sell'
      assess = `RSI at ${rsi.toFixed(1)} is in the neutral-to-bearish zone (30–50). Momentum is softening — buyers have not regained the upper hand. A recovery above 50 would signal momentum turning constructive again. Below 50 the path of least resistance is lower.`
    } else {
      assess = `RSI at ${rsi.toFixed(1)} is in the neutral zone (45–55). No strong directional signal. Watch for a break above 55 (bullish) or below 45 (bearish) for the next momentum cue.`
    }
    cards.push({ label: 'RSI (14)', value: rsi.toFixed(1), signal, note: zone || null, assess })
  }

  if (macd != null) {
    const isBull = state.includes('bull')
    const hist   = macd - (sigV ?? macd)
    const signal = isBull ? 'buy' : 'sell'
    const assess = isBull
      ? `MACD line (${fmtV(macd)}) is above the signal line (${fmtV(sigV)}), histogram ${hist >= 0 ? '+' : ''}${hist.toFixed(3)}. Bullish momentum configuration. Watch whether histogram bars are growing (accelerating) or shrinking (decelerating but still positive) — a histogram moving toward zero warns of a potential bearish crossover ahead.`
      : `MACD line (${fmtV(macd)}) is below the signal line (${fmtV(sigV)}), histogram ${hist.toFixed(3)}. Momentum is bearish. In this configuration rallies tend to be shallow. A sustained move of the MACD line back above the signal line is needed to confirm recovery.`
    cards.push({ label: 'MACD (12,26,9)', value: fmtV(macd), signal, note: state || null, assess })
  }

  if (trend) {
    const isGolden = trend.includes('golden')
    const signal   = isGolden ? 'buy' : 'sell'
    const assess   = isGolden
      ? `The 50-day SMA (${fmtV(i.sma50)}) is above the 200-day SMA (${fmtV(i.sma200)}) — a golden cross. This long-term bullish structure means recent momentum is outpacing the longer-term trend. Institutional investors use this as a trend confirmation signal. As long as this structure holds, the path of least resistance is higher.`
      : `The 50-day SMA (${fmtV(i.sma50)}) is below the 200-day SMA (${fmtV(i.sma200)}) — a death cross. This is a long-term bearish signal; recent momentum has fallen below the longer-term trend. Recovery above the 200-day SMA would be the first step toward a structural reversal.`
    cards.push({ label: 'Trend (SMA 50 / 200)', value: isGolden ? 'Golden Cross' : 'Death Cross', signal, note: null, assess })
  }

  if (p50 != null) {
    const above  = p50 > 0
    const signal = above ? 'buy' : 'sell'
    let assess = ''
    if (above && p50 > 15) assess = `Price is ${p50.toFixed(1)}% above its 50-day SMA (${fmtV(i.sma50)}). A significant extension from the short-term average — the stock has strong momentum but may be due for consolidation. In a persistent trend the 50-day rises to close the gap, but the risk/reward for new entries is more stretched at these levels.`
    else if (above) assess = `Price is ${p50.toFixed(1)}% above its 50-day SMA (${fmtV(i.sma50)}). Intermediate-term bullish — the 50-day acts as dynamic support on pullbacks. This reading is constructive without being over-extended.`
    else if (p50 > -10) assess = `Price is ${Math.abs(p50).toFixed(1)}% below its 50-day SMA (${fmtV(i.sma50)}). Short-term momentum is negative; the 50-day is now acting as overhead resistance. A recovery back above it on strong volume would reassert a bullish short-term bias.`
    else assess = `Price is ${Math.abs(p50).toFixed(1)}% below its 50-day SMA (${fmtV(i.sma50)}). A meaningful breakdown from the short-term average. Until price recaptures the 50-day, rallies are likely to face resistance at that level.`
    cards.push({ label: 'Price vs SMA 50', value: `${p50 >= 0 ? '+' : ''}${p50.toFixed(1)}%`, signal, note: null, assess })
  }

  if (p200 != null) {
    const above  = p200 > 0
    const signal = above ? 'buy' : 'sell'
    let assess = ''
    if (above && p200 > 30) assess = `Price is ${p200.toFixed(1)}% above its 200-day SMA (${fmtV(i.sma200)}). An extreme reading indicating a powerful long-term uptrend. The 200-day rising beneath the price is the hallmark of a genuine secular bull run. As long as price holds above it, the long-term trend is decisively up.`
    else if (above) assess = `Price is ${p200.toFixed(1)}% above its 200-day SMA (${fmtV(i.sma200)}). The single most important long-term trend confirmation signal — bulls have structural control. This is the ultimate support line: as long as the stock holds above it, the multi-month trend is constructively up.`
    else if (p200 > -10) assess = `Price is ${Math.abs(p200).toFixed(1)}% below its 200-day SMA (${fmtV(i.sma200)}). The stock has lost its long-term bullish structure. Trading below the 200-day is a significant warning. A weekly close back above it is the minimum to reassert a bullish long-term view.`
    else assess = `Price is ${Math.abs(p200).toFixed(1)}% below its 200-day SMA (${fmtV(i.sma200)}). A deep breakdown from the 200-day SMA signals that the long-term trend has turned decidedly bearish. Recovery typically takes months and requires a substantial catalyst or valuation reset.`
    cards.push({ label: 'Price vs SMA 200', value: `${p200 >= 0 ? '+' : ''}${p200.toFixed(1)}%`, signal, note: null, assess })
  }

  return cards
}

/* ───────────── charts ───────────── */
function PriceChart({ data, cur, showSMA, showBB, height = 200 }) {
  if (!data.length) return null
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <XAxis dataKey="date" hide />
        <YAxis domain={['auto', 'auto']} tick={{ fill: T.muted, fontSize: 10, fontFamily: T.mono }} width={52} />
        <Tooltip contentStyle={tip} labelStyle={{ color: T.muted }} formatter={v => fmtMoney(v, cur)} />
        {showBB && <Area dataKey="bbUpper" stroke="none" fill={T.cyan} fillOpacity={0.05} isAnimationActive={false} />}
        {showBB && <Area dataKey="bbLower" stroke="none" fill={T.bg} fillOpacity={1} isAnimationActive={false} />}
        <Line dataKey="close" stroke={T.amber} dot={false} strokeWidth={1.6} isAnimationActive={false} />
        {Object.entries(SMA_META).map(([k, meta]) => showSMA[k] && (
          <Line key={k} dataKey={k} stroke={meta.color} dot={false} strokeWidth={1} strokeDasharray="3 3" isAnimationActive={false} />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

function RsiChart({ data }) {
  if (!data.length) return null
  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <XAxis dataKey="date" hide />
        <YAxis domain={[0, 100]} ticks={[30, 50, 70]} tick={{ fill: T.muted, fontSize: 10, fontFamily: T.mono }} width={52} />
        <Tooltip contentStyle={tip} labelStyle={{ color: T.muted }} />
        <ReferenceLine y={70} stroke={T.red} strokeDasharray="3 3" />
        <ReferenceLine y={30} stroke={T.green} strokeDasharray="3 3" />
        <Area dataKey="rsi" stroke={T.purple} fill={T.purple} fillOpacity={0.12} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function MacdChart({ data }) {
  if (!data.length) return null
  return (
    <ResponsiveContainer width="100%" height={120}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <XAxis dataKey="date" hide />
        <YAxis tick={{ fill: T.muted, fontSize: 10, fontFamily: T.mono }} width={52} />
        <Tooltip contentStyle={tip} labelStyle={{ color: T.muted }} />
        <ReferenceLine y={0} stroke={T.border} />
        <Bar dataKey="macdHist" isAnimationActive={false}>
          {data.map((d, i) => <Cell key={i} fill={(d.macdHist ?? 0) >= 0 ? T.green : T.red} />)}
        </Bar>
        <Line dataKey="macd" stroke={T.cyan} dot={false} strokeWidth={1.2} isAnimationActive={false} />
        <Line dataKey="macdSignal" stroke={T.amber} dot={false} strokeWidth={1.2} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

function VolumeChart({ data }) {
  if (!data.length) return null
  return (
    <ResponsiveContainer width="100%" height={100}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <XAxis dataKey="date" hide />
        <YAxis tick={{ fill: T.muted, fontSize: 10, fontFamily: T.mono }} width={52} tickFormatter={v => `${(v / 1e6).toFixed(0)}M`} />
        <Tooltip contentStyle={tip} labelStyle={{ color: T.muted }} formatter={v => `${(v / 1e6).toFixed(2)}M`} />
        <Bar dataKey="volume" fill={T.dim} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/* ───────────── tabs ───────────── */
function OverviewTab({ data, chart }) {
  const { meta, quote, fundamentals: f, analyst: a, indicators: ind } = data
  const cur = meta.currency
  const [showSMA] = useState({ sma50: true, sma200: true })
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 8 }}>
        <KPI label="MARKET CAP" value={fmtCap(f.market_cap)} />
        <KPI label="P/E (TTM)" value={fmtMult(f.trailing_pe)} />
        <KPI label="FORWARD P/E" value={fmtMult(f.forward_pe)} />
        <KPI label="BETA" value={fmt(f.beta)} />
        <KPI label="52W HIGH" value={fmtMoney(quote.fifty_two_week_high, cur)} />
        <KPI label="52W LOW" value={fmtMoney(quote.fifty_two_week_low, cur)} />
      </div>
      <Panel title={`Price — last ${chart.length} sessions`}>
        <div style={{ padding: 10 }}><PriceChart data={chart} cur={cur} showSMA={showSMA} showBB={false} /></div>
      </Panel>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 14 }}>
        <Panel title="Snapshot">
          <Row m="Sector" v={meta.sector || '—'} />
          <Row m="Industry" v={meta.industry || '—'} />
          <Row m="Trend" v={ind?.trend ? ind.trend.split(' ')[0] : '—'} />
          <Row m="RSI (14)" v={fmt(ind?.rsi, 1)} s={ind?.rsi_zone === 'overbought' ? 'sell' : ind?.rsi_zone === 'oversold' ? 'buy' : null} />
          <Row m="Dividend Yield" v={f.dividend_yield != null ? fmtFrac(f.dividend_yield) : '—'} />
        </Panel>
        <Panel title="Analyst Consensus">
          <Row m="Recommendation" v={(a.recommendation || '—').toUpperCase()} />
          <Row m="# Analysts" v={fmt(a.num_analysts, 0)} />
          <Row m="Mean Target" v={fmtMoney(a.target_mean, cur)} />
          <Row m="Implied Upside" v={fmtPct(a.target_upside_pct)} s={num(a.target_upside_pct) >= 0 ? 'buy' : 'sell'} />
          <Row m="Target Range" v={`${fmt(a.target_low)} – ${fmt(a.target_high)}`} />
        </Panel>
      </div>
    </div>
  )
}

function FundamentalsTab({ data }) {
  const f = data.fundamentals
  const rows = [
    ['Market Cap', fmtCap(f.market_cap)],
    ['Trailing P/E', fmtMult(f.trailing_pe)],
    ['Forward P/E', fmtMult(f.forward_pe)],
    ['PEG Ratio', fmt(f.peg_ratio)],
    ['Beta', fmt(f.beta)],
    ['Profit Margin', fmtFrac(f.profit_margin)],
    ['Revenue Growth (YoY)', fmtFrac(f.revenue_growth)],
    ['Earnings Growth (YoY)', fmtFrac(f.earnings_growth)],
    ['Dividend Yield', f.dividend_yield != null ? fmtFrac(f.dividend_yield) : '—'],
  ]
  return (
    <Panel title="Fundamentals">
      {rows.map(([m, v]) => <Row key={m} m={m} v={v} />)}
      <div style={{ padding: '8px 13px', fontSize: 10, color: T.dim, fontFamily: T.mono }}>
        Source: {data.data_source}. Fields shown as “—” are not provided by the free data feed.
      </div>
    </Panel>
  )
}

function TechnicalsTab({ data }) {
  const i    = data.indicators || {}
  const cur  = data.meta.currency
  const [view, setView] = useState('assessed')

  const indCards = buildIndicatorCards(i, data.quote || {})
  const bullN    = indCards.filter(c => c.signal === 'buy').length
  const bearN    = indCards.filter(c => c.signal === 'sell').length
  const netScore = bullN - bearN
  const overall  = netScore >= 2 ? 'buy' : netScore <= -2 ? 'sell' : 'hold'
  const overallC = sc(overall)

  const smaLevels = [
    ['SMA 50',  fmtMoney(i.sma50,  cur), i.price_vs_sma50_pct],
    ['SMA 200', fmtMoney(i.sma200, cur), i.price_vs_sma200_pct],
  ]

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {/* Composite score banner */}
      <div style={{ border: `1px solid ${overallC}44`, background: overallC + '0d', borderRadius: 8,
        padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 4 }}>TECHNICAL COMPOSITE</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Badge s={overall} label={overall.toUpperCase()} />
            <span style={{ fontSize: 12, color: T.muted, fontFamily: T.mono }}>
              {bullN} bullish · {bearN} bearish · {indCards.length - bullN - bearN} neutral
            </span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 3, flex: 1, minWidth: 80 }}>
          {indCards.map((c, idx) => (
            <div key={idx} title={c.label} style={{ flex: 1, height: 5, background: sc(c.signal), borderRadius: 2, opacity: 0.8 }} />
          ))}
        </div>
        <div style={{ display: 'flex', gap: 5 }}>
          {['assessed', 'compact'].map(v => (
            <button key={v} onClick={() => setView(v)} style={{
              fontSize: 10, fontFamily: T.mono, cursor: 'pointer',
              border: `1px solid ${view === v ? T.amber : T.border}`,
              background: view === v ? T.amberDim : 'transparent',
              color: view === v ? T.amber : T.muted, padding: '2px 8px', borderRadius: 3,
            }}>{v === 'assessed' ? 'ASSESSED' : 'COMPACT'}</button>
          ))}
        </div>
      </div>

      {/* Assessed view — one card per indicator with explanation */}
      {view === 'assessed' && (
        <div style={{ display: 'grid', gap: 10 }}>
          {indCards.map((c, idx) => <AssessCard key={idx} {...c} />)}
        </div>
      )}

      {/* Compact view — original table layout */}
      {view === 'compact' && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 8 }}>
            {[
              { l: 'RSI (14)', v: fmt(i.rsi, 1), s: i.rsi_zone },
              { l: 'MACD',    v: fmt(i.macd, 3), s: i.macd_state?.startsWith('bull') ? 'buy' : i.macd_state ? 'sell' : null },
              { l: 'Trend',   v: i.trend ? i.trend.split(' ')[0] : '—', s: i.trend?.startsWith('golden') ? 'buy' : i.trend ? 'sell' : null },
            ].map(({ l, v, s }) => (
              <div key={l} style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 6, padding: '12px 14px' }}>
                <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 6 }}>{l}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 18, color: T.text, fontFamily: T.mono, fontWeight: 700 }}>{v}</span>
                  {s && <Badge s={s} />}
                </div>
              </div>
            ))}
          </div>
          <Panel title="Moving Averages">
            {smaLevels.map(([m, v, pct]) => (
              <Row key={m} m={m} v={v} note={pct != null ? `price ${fmtPct(pct)}` : ''} />
            ))}
          </Panel>
        </>
      )}
    </div>
  )
}

function ChartsTab({ data, chart }) {
  const [showSMA, setShowSMA] = useState({ sma20: false, sma50: true, sma100: false, sma200: true })
  const [showBB, setShowBB] = useState(false)
  const cur = data.meta.currency
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {Object.entries(SMA_META).map(([k, meta]) => (
          <button key={k} onClick={() => setShowSMA(p => ({ ...p, [k]: !p[k] }))}
            style={{ fontSize: 11, fontFamily: T.mono, fontWeight: 700, padding: '4px 10px', borderRadius: 4, cursor: 'pointer',
              background: showSMA[k] ? meta.color + '22' : T.surface, color: showSMA[k] ? meta.color : T.muted,
              border: `1px solid ${showSMA[k] ? meta.color + '66' : T.border}` }}>{meta.label}</button>
        ))}
        <button onClick={() => setShowBB(v => !v)}
          style={{ fontSize: 11, fontFamily: T.mono, fontWeight: 700, padding: '4px 10px', borderRadius: 4, cursor: 'pointer',
            background: showBB ? T.cyan + '22' : T.surface, color: showBB ? T.cyan : T.muted, border: `1px solid ${showBB ? T.cyan + '66' : T.border}` }}>BOLLINGER</button>
      </div>
      <Panel title="Price"><div style={{ padding: 10 }}><PriceChart data={chart} cur={cur} showSMA={showSMA} showBB={showBB} height={260} /></div></Panel>
      <Panel title="RSI (14)"><div style={{ padding: 10 }}><RsiChart data={chart} /></div></Panel>
      <Panel title="MACD (12,26,9)"><div style={{ padding: 10 }}><MacdChart data={chart} /></div></Panel>
      <Panel title="Volume"><div style={{ padding: 10 }}><VolumeChart data={chart} /></div></Panel>
    </div>
  )
}

function AssessmentTab({ data }) {
  const a = data.assessment
  if (!a) return <Panel title="AI Assessment"><div style={{ padding: 16, color: T.muted, fontSize: 13 }}>{data.ai_error || 'No assessment available.'}</div></Panel>
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: 8 }}>
        <KPI label="SIGNAL" value={(a.signal || '—').toUpperCase()} />
        <KPI label="CONVICTION" value={(a.conviction || '—').toUpperCase()} />
        <KPI label="RISK LEVEL" value={(a.risk_level || '—').toUpperCase()} />
        <KPI label="HORIZON" value={a.time_horizon || '—'} />
      </div>
      <Panel title="Summary"><div style={{ padding: 14, fontSize: 13, lineHeight: 1.7, color: T.text }}>{a.summary}</div></Panel>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 14 }}>
        <Panel title="Bullish Factors">
          {(a.bullish_factors || []).map((x, i) => <div key={i} style={{ padding: '8px 13px', fontSize: 12, color: T.text, borderBottom: `1px solid ${T.border}` }}>↗ {x}</div>)}
        </Panel>
        <Panel title="Bearish Factors">
          {(a.bearish_factors || []).map((x, i) => <div key={i} style={{ padding: '8px 13px', fontSize: 12, color: T.text, borderBottom: `1px solid ${T.border}` }}>↘ {x}</div>)}
        </Panel>
      </div>
      {a.technical_read && <Panel title="Technical Read"><div style={{ padding: 14, fontSize: 13, lineHeight: 1.7, color: T.text }}>{a.technical_read}</div></Panel>}
      {a.reasoning && <Panel title="Reasoning"><div style={{ padding: 14, fontSize: 13, lineHeight: 1.7, color: T.text }}>{a.reasoning}</div></Panel>}
      <div style={{ fontSize: 11, color: T.dim, fontFamily: T.mono }}>
        Engine: {a.engine || data.engine || 'rules'}{a.model ? ` (${a.model})` : ''} · {a.disclaimer || data.disclaimer}
      </div>
    </div>
  )
}

const SENT = { POS: 'Positive', NEG: 'Negative', NEU: 'Neutral' }
function NewsTab({ data }) {
  const news = data.news || []
  if (!news.length) return <Panel title="News"><div style={{ padding: 16, color: T.muted, fontSize: 13 }}>No recent headlines available for {data.meta.ticker}.</div></Panel>
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {news.map((n, i) => (
        <div key={i} style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: '14px 16px', display: 'flex', gap: 14 }}>
          <div style={{ width: 4, borderRadius: 2, background: sc(n.sentiment), flexShrink: 0, alignSelf: 'stretch', minHeight: 50 }} />
          <div style={{ flex: 1 }}>
            <a href={n.link || '#'} target="_blank" rel="noreferrer" style={{ fontSize: 14, color: T.text, fontWeight: 600, lineHeight: 1.42, textDecoration: 'none' }}>{n.title}</a>
            {n.summary && <p style={{ fontSize: 12, color: T.muted, margin: '6px 0 8px', lineHeight: 1.6 }}>{n.summary}</p>}
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 6 }}>
              {n.publisher && <span style={{ fontSize: 11, fontFamily: T.mono, fontWeight: 700, color: T.amber }}>{n.publisher}</span>}
              {n.published && <span style={{ fontSize: 11, fontFamily: T.mono, color: T.dim }}>{n.published}</span>}
              <Badge s={n.sentiment} label={SENT[n.sentiment]} />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function PeersTab({ data }) {
  const peers = data.peers || []
  const hdrs = ['TICKER', 'COMPANY', 'PRICE', 'CHG %', 'MKT CAP', 'P/E', 'MARGIN']
  if (!peers.length) return <Panel title="Peers"><div style={{ padding: 16, color: T.muted, fontSize: 13 }}>No peer comparison data available for {data.meta.ticker}.</div></Panel>
  return (
    <Panel title={`Sector Peers — ${data.meta.sector || ''}`}>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.025)', borderBottom: `1px solid ${T.border}` }}>
              {hdrs.map(h => <th key={h} style={{ padding: '9px 13px', textAlign: 'left', fontSize: 10, color: T.amber, fontFamily: T.head, fontWeight: 700, whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {peers.map((p, i) => (
              <tr key={i} style={{ borderBottom: `1px solid ${T.border}`, background: p.ticker === data.meta.ticker ? T.amberDim : 'transparent' }}>
                <td style={{ padding: '9px 13px', fontSize: 13, fontFamily: T.mono, color: T.amber, fontWeight: 700 }}>{p.ticker}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, color: T.text, whiteSpace: 'nowrap' }}>{p.name}</td>
                <td style={{ padding: '9px 13px', fontSize: 13, fontFamily: T.mono, color: T.text, fontWeight: 700 }}>{fmt(p.price)}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, fontFamily: T.mono, fontWeight: 700, color: num(p.change_pct) >= 0 ? T.green : T.red }}>{fmtPct(p.change_pct)}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, fontFamily: T.mono, color: T.muted }}>{fmtCap(p.market_cap)}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, fontFamily: T.mono, color: T.muted }}>{fmtMult(p.trailing_pe)}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, fontFamily: T.mono, color: T.muted }}>{fmtFrac(p.profit_margin)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function ChatTab({ ticker, aiConfigured }) {
  const [msgs, setMsgs] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)
  useEffect(() => {
    setMsgs([{ role: 'ai', text: `Hi! Ask me anything about ${ticker} — valuation, risks, momentum, or the analyst consensus. I answer from the live data snapshot.` }])
  }, [ticker])
  useEffect(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), [msgs])

  const SUGGEST = [
    `What's the investment thesis for ${ticker}?`,
    `Is ${ticker} overvalued right now?`,
    `What are the key risks for ${ticker}?`,
    `How does the technical picture look for ${ticker}?`,
  ]

  const send = async (text) => {
    const q = (text || input).trim()
    if (!q || busy) return
    setInput('')
    const history = msgs
    setMsgs(p => [...p, { role: 'user', text: q }])
    setBusy(true)
    try {
      const data = await chatAboutTicker(ticker, q, history)
      setMsgs(p => [...p, { role: 'ai', text: data.reply }])
    } catch (e) {
      setMsgs(p => [...p, { role: 'ai', text: `⚠️ ${e.message}` }])
    }
    setBusy(false)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 540, gap: 10 }}>
      {!aiConfigured && (
        <div style={{ background: T.amberDim, border: `1px solid ${T.amberBorder}`, borderRadius: 6, padding: '8px 12px', fontSize: 12, color: T.amber, fontFamily: T.mono }}>
          AI chat needs an LLM key on the server. Without one, requests will return a 503.
        </div>
      )}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 11, paddingRight: 4 }}>
        {msgs.map((m, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', gap: 9 }}>
            {m.role === 'ai' && <div style={{ width: 28, height: 28, borderRadius: '50%', background: T.amberDim, border: `1px solid ${T.amber}55`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, flexShrink: 0, marginTop: 3 }}>⚡</div>}
            <div style={{ maxWidth: '72%', padding: '10px 14px', borderRadius: 8, fontSize: 13, lineHeight: 1.7,
              background: m.role === 'user' ? T.amberDim : T.surface, border: `1px solid ${m.role === 'user' ? T.amberBorder : T.border}`,
              color: T.text, whiteSpace: 'pre-wrap', fontFamily: m.role === 'user' ? T.mono : 'inherit' }}>{m.text}</div>
          </div>
        ))}
        {busy && <div style={{ fontSize: 12, color: T.muted, fontFamily: T.mono, paddingLeft: 38 }}>thinking…</div>}
        <div ref={endRef} />
      </div>
      {msgs.length <= 1 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {SUGGEST.map((s, i) => <button key={i} onClick={() => send(s)} style={{ fontSize: 11, fontFamily: T.mono, color: T.muted, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 20, padding: '5px 11px', cursor: 'pointer' }}>{s}</button>)}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
          placeholder={`Ask the analyst about ${ticker}...`}
          style={{ flex: 1, background: T.surface, border: `1px solid ${T.borderMid}`, borderRadius: 8, padding: '10px 14px', color: T.text, fontSize: 13, fontFamily: T.mono, outline: 'none' }} />
        <button onClick={() => send()} disabled={busy || !input.trim()}
          style={{ background: busy || !input.trim() ? T.amberDim : T.amber, border: 'none', borderRadius: 8, padding: '0 20px', color: busy || !input.trim() ? T.amber : '#000', fontSize: 12, fontFamily: T.mono, fontWeight: 700, cursor: busy ? 'not-allowed' : 'pointer' }}>SEND ↑</button>
      </div>
    </div>
  )
}

/* ───────────── main app ───────────── */
const TABS = [
  { id: 'overview', label: 'OVERVIEW' },
  { id: 'fundamentals', label: 'FUNDAMENTALS' },
  { id: 'technicals', label: 'TECHNICALS' },
  { id: 'charts', label: 'CHARTS' },
  { id: 'news', label: 'NEWS' },
  { id: 'peers', label: 'PEERS' },
  { id: 'assessment', label: 'ASSESSMENT' },
  { id: 'chat', label: '⚡ AI ANALYST' },
]

export default function App() {
  const [search, setSearch] = useState('')
  const [ticker, setTicker] = useState('')
  const [data, setData] = useState(null)
  const [chart, setChart] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('overview')

  async function run(sym) {
    const s = (sym ?? search).trim().toUpperCase()
    if (!s) return
    setLoading(true); setError(null)
    try {
      const result = await analyzeTicker(s)
      setData(result)
      setChart(buildSeries(result.series || {}))
      setTicker(s)
      setTab('overview')
    } catch (e) {
      setError(e.message); setData(null); setChart([])
    } finally {
      setLoading(false)
    }
  }

  const quote = data?.quote
  const pos = num(quote?.day_change_pct) >= 0

  return (
    <div style={{ background: T.bg, minHeight: '100vh', color: T.text }}>
      <style>{`
        *{box-sizing:border-box;}
        ::-webkit-scrollbar{width:4px;height:4px;}
        ::-webkit-scrollbar-track{background:${T.surface};}
        ::-webkit-scrollbar-thumb{background:#2d3748;border-radius:3px;}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
        input::placeholder{color:${T.dim};}
      `}</style>

      {/* top nav */}
      <div style={{ background: T.nav, borderBottom: `1px solid ${T.border}`, padding: '0 24px', height: 50, display: 'flex', alignItems: 'center', gap: 20, position: 'sticky', top: 0, zIndex: 100 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <span style={{ fontFamily: T.head, fontWeight: 700, fontSize: 20, color: T.amber }}>ANALYST</span>
          <span style={{ fontFamily: T.head, fontSize: 20, color: T.muted }}>AGENT</span>
        </div>
        <div style={{ display: 'flex', gap: 6, flex: 1, maxWidth: 420 }}>
          <input value={search} onChange={e => setSearch(e.target.value.toUpperCase())} onKeyDown={e => e.key === 'Enter' && run()}
            placeholder="Search ticker (AAPL, TSLA, NVDA...)"
            style={{ flex: 1, background: '#0f121a', border: `1px solid ${T.border}`, borderRadius: 6, padding: '6px 12px', color: T.text, fontSize: 12, fontFamily: T.mono, outline: 'none' }} />
          <button onClick={() => run()} disabled={loading}
            style={{ background: T.amberDim, border: `1px solid ${T.amberBorder}`, borderRadius: 6, padding: '6px 13px', color: T.amber, fontSize: 11, fontFamily: T.mono, fontWeight: 700, cursor: 'pointer' }}>
            {loading ? '…' : 'ANALYZE'}
          </button>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: T.green, animation: 'pulse 2s infinite' }} />
          <span style={{ fontSize: 11, fontFamily: T.mono, color: T.muted }}>{data?.engine || 'ready'}</span>
        </div>
      </div>

      {/* hero bar */}
      {data && (
        <div style={{ background: T.nav, borderBottom: `1px solid ${T.border}`, padding: '13px 24px', display: 'flex', alignItems: 'center', gap: 30, flexWrap: 'wrap' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span style={{ fontFamily: T.head, fontWeight: 700, fontSize: 28, color: T.amber }}>{data.meta.ticker}</span>
              <span style={{ fontSize: 13, color: T.muted }}>{data.meta.name}</span>
            </div>
            <div style={{ fontSize: 11, color: T.dim, fontFamily: T.mono }}>{[data.meta.exchange, data.meta.sector].filter(Boolean).join(' · ')}</div>
          </div>
          <div style={{ borderLeft: `1px solid ${T.border}`, paddingLeft: 30 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
              <span style={{ fontFamily: T.mono, fontWeight: 700, fontSize: 30, color: T.text }}>{fmtMoney(quote.price, data.meta.currency)}</span>
              <div style={{ fontFamily: T.mono, fontWeight: 700, fontSize: 14, color: pos ? T.green : T.red }}>{fmtPct(quote.day_change_pct)}</div>
            </div>
          </div>
          <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
            <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 3 }}>ANALYST CONSENSUS</div>
            <div style={{ fontFamily: T.head, fontWeight: 700, fontSize: 17, color: T.green }}>{(data.analyst.recommendation || '—').toUpperCase()}</div>
            <div style={{ fontSize: 11, color: T.muted, fontFamily: T.mono }}>{fmt(data.analyst.num_analysts, 0)} analysts · PT {fmt(data.analyst.target_mean)}</div>
          </div>
        </div>
      )}

      {/* tabs */}
      {data && (
        <div style={{ background: T.nav, borderBottom: `1px solid ${T.border}`, padding: '0 24px', display: 'flex', overflowX: 'auto' }}>
          {TABS.map(({ id, label }) => (
            <button key={id} onClick={() => setTab(id)}
              style={{ padding: '11px 16px', fontSize: 12, fontFamily: T.head, fontWeight: 700, cursor: 'pointer', border: 'none', outline: 'none', whiteSpace: 'nowrap',
                background: 'transparent', color: tab === id ? T.amber : T.muted, borderBottom: tab === id ? `2px solid ${T.amber}` : '2px solid transparent' }}>{label}</button>
          ))}
        </div>
      )}

      {/* content */}
      <div style={{ padding: '20px 24px 72px', maxWidth: 1120 }}>
        {error && <div style={{ background: 'rgba(241,106,106,0.1)', border: `1px solid ${T.red}55`, borderRadius: 8, padding: '12px 16px', color: T.red, fontSize: 13 }}>{error}</div>}
        {loading && !data && <div style={{ padding: 60, textAlign: 'center', color: T.muted }}>Analyzing {search || '…'}…</div>}
        {!data && !loading && !error && (
          <div style={{ padding: 80, textAlign: 'center', color: T.muted }}>
            <div style={{ fontSize: 16, marginBottom: 8 }}>Enter a ticker symbol to begin.</div>
            <div style={{ fontSize: 13, color: T.dim }}>Try AAPL, MSFT, NVDA, TSLA…</div>
          </div>
        )}
        {data && (
          <>
            {tab === 'overview' && <OverviewTab data={data} chart={chart} />}
            {tab === 'fundamentals' && <FundamentalsTab data={data} />}
            {tab === 'technicals' && <TechnicalsTab data={data} />}
            {tab === 'charts' && <ChartsTab data={data} chart={chart} />}
            {tab === 'news' && <NewsTab data={data} />}
            {tab === 'peers' && <PeersTab data={data} />}
            {tab === 'assessment' && <AssessmentTab data={data} />}
            {tab === 'chat' && <ChatTab ticker={data.meta.ticker} aiConfigured={!!data.engine && data.engine !== 'rules'} />}
          </>
        )}
      </div>

      {/* status bar */}
      <div style={{ background: '#040408', borderTop: `1px solid ${T.amberBorder}`, padding: '5px 24px', display: 'flex', alignItems: 'center', position: 'fixed', bottom: 0, left: 0, right: 0, gap: 12 }}>
        <span style={{ fontSize: 10, fontFamily: T.mono, color: T.amber }}>⚡ ANALYST AGENT</span>
        {data && <span style={{ fontSize: 10, fontFamily: T.mono, color: T.dim }}>· {data.data_source} · {data.as_of}</span>}
        <span style={{ marginLeft: 'auto', fontSize: 10, fontFamily: T.mono, color: T.dim }}>Educational only · Not financial advice</span>
      </div>
    </div>
  )
}
