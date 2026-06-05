import { useState, useRef, useEffect } from 'react'
import {
  AreaChart, Area, ComposedChart, Bar, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts'
import { analyzeTicker, chatAboutTicker, searchSymbols } from './api'

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

// Currency symbol map — disambiguates the many "$" currencies (US$, S$, HK$, A$…)
// and covers the major exchanges this app reaches. Unknown codes fall back to the
// ISO code as a prefix (e.g. "PLN 1.2B") so nothing is ever mislabelled as USD.
const CUR_SYM = {
  USD: '$', EUR: '€', GBP: '£', GBp: 'p', JPY: '¥', CNY: '¥', CNH: '¥',
  HKD: 'HK$', SGD: 'S$', MYR: 'RM', IDR: 'Rp', THB: '฿', INR: '₹', PHP: '₱',
  VND: '₫', TWD: 'NT$', KRW: '₩', AUD: 'A$', CAD: 'C$', NZD: 'NZ$', CHF: 'CHF ',
  SEK: 'kr ', NOK: 'kr ', DKK: 'kr ', ZAR: 'R', BRL: 'R$', MXN: 'MX$',
  AED: 'AED ', SAR: 'SAR ', TRY: '₺', RUB: '₽', PLN: 'zł ',
}
const curSym = c => CUR_SYM[c] || (c ? c + ' ' : '$')

// Market cap with the correct currency symbol (defaults to USD only if unknown).
const fmtCap = (v, cur = 'USD') => {
  const n = num(v); if (n == null) return '—'
  const s = curSym(cur)
  if (n >= 1e12) return `${s}${(n / 1e12).toFixed(2)}T`
  if (n >= 1e9) return `${s}${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `${s}${(n / 1e6).toFixed(2)}M`
  return `${s}${n.toLocaleString()}`
}
const fmtMoney = (v, cur = '') => { const n = num(v); return n == null ? '—' : `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}${cur ? ' ' + cur : ''}` }

/* ───────────── analyst-coverage classification ───────────── */
// Distinguishes "no/thin analyst coverage" (small-caps Wall St doesn't follow)
// from "data is broken". A blank consensus on a 2-analyst micro-cap is expected,
// not an error — surface that so users read "—" correctly.
function coverageInfo(numAnalysts) {
  const c = num(numAnalysts)
  if (c == null || c === 0) {
    return {
      level: 'none', label: 'NOT COVERED', color: '#5e6573',
      note: 'No Wall-Street analysts currently publish ratings or price targets for this stock. ' +
            'Analyst-consensus metrics are simply unavailable — lean on the technical and ' +
            'fundamental data instead. This is normal for micro-caps, foreign listings, and ETFs.',
    }
  }
  if (c < 5) {
    return {
      level: 'low', label: 'LOW COVERAGE', color: '#f97316',
      note: `Only ${c} analyst${c === 1 ? '' : 's'} cover${c === 1 ? 's' : ''} this stock. ` +
            'Consensus figures (recommendation, mean target) come from a very small sample, so they ' +
            'can be volatile or unrepresentative — weight them lightly and prioritise the fundamentals.',
    }
  }
  if (c < 10) {
    return {
      level: 'moderate', label: 'MODERATE COVERAGE', color: '#e6a72a',
      note: `${c} analysts cover this stock — a modest sample. Consensus is meaningful but not deep; ` +
            'a single rating change can move the average.',
    }
  }
  return { level: 'good', label: null, color: '#2dd4a0', note: null }
}

const CoverageBanner = ({ numAnalysts, compact }) => {
  const cov = coverageInfo(numAnalysts)
  if (!cov.note) return null  // good coverage → no banner
  return (
    <div style={{ background: cov.color + '12', border: `1px solid ${cov.color}40`, borderRadius: 8,
      padding: compact ? '8px 12px' : '10px 14px', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <span style={{ fontSize: 13, lineHeight: 1.2, flexShrink: 0 }}>{cov.level === 'none' ? '○' : '⚠'}</span>
      <div>
        <span style={{ fontSize: 10, fontFamily: T.mono, fontWeight: 700, color: cov.color,
          letterSpacing: '0.04em', textTransform: 'uppercase' }}>{cov.label}</span>
        <div style={{ fontSize: 11, lineHeight: 1.65, color: T.muted, fontFamily: T.mono, marginTop: 3 }}>{cov.note}</div>
      </div>
    </div>
  )
}

// Small inline coverage chip for the hero bar / panel headers.
const CoverageChip = ({ numAnalysts }) => {
  const cov = coverageInfo(numAnalysts)
  if (!cov.label) return null
  return (
    <span style={{ fontSize: 9, fontFamily: T.mono, fontWeight: 700, color: cov.color,
      background: cov.color + '1a', border: `1px solid ${cov.color}44`, padding: '1px 6px',
      borderRadius: 3, letterSpacing: '0.04em', whiteSpace: 'nowrap' }}>{cov.label}</span>
  )
}

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

/* ═══════════════════════════════════════════════════════════
   QUANTITATIVE RISK / OPPORTUNITY ENGINE
   Each metric is scored on two independent 0-10 axes:
     risk  — downside / valuation stretch / structural weakness
     opp   — upside / mean-reversion / entry quality
   R/R ratio = opp / risk  (higher = better risk-adjusted reward)
═══════════════════════════════════════════════════════════ */
function computeROR(data) {
  const f   = data.fundamentals || {}
  const i   = data.indicators   || {}
  const a   = data.analyst      || {}
  const q   = data.quote        || {}
  const n   = v => (v != null && !Number.isNaN(Number(v))) ? Number(v) : null
  const asPct = v => { const x = n(v); if (x == null) return null; return Math.abs(x) < 1.5 ? x * 100 : x }

  const valItems = [], techItems = [], fundItems = [], sentItems = []

  // ── VALUATION ────────────────────────────────────
  const pe = n(f.trailing_pe)
  if (pe != null && pe > 0) {
    let r, o, v, c
    if      (pe > 80) { r=5; o=0; v='high-risk';   c='Extreme valuation (P/E>80). Requires sustained hypergrowth — any earnings miss risks sharp multiple compression. Asymmetric downside.' }
    else if (pe > 50) { r=3; o=0; v='caution';     c='Rich valuation (P/E>50). Priced for perfection. Downside risk is asymmetric if growth slows even slightly.' }
    else if (pe > 30) { r=2; o=1; v='caution';     c='Elevated multiple (P/E>30). Growth premium is baked in. Only justified for high-quality compounders with durable, visible earnings.' }
    else if (pe > 15) { r=1; o=2; v='neutral';     c='Fair-to-reasonable valuation (P/E 15-30). Neither deeply cheap nor dangerously expensive. Balanced risk/reward depends on growth quality.' }
    else              { r=0; o=4; v='opportunity';  c='Cheap valuation (P/E<15). Market is pricing in risk — if fears are overdone, meaningful re-rating potential exists. Classic value opportunity.' }
    valItems.push({ metric: 'Trailing P/E', value: pe.toFixed(1)+'×', rPts: r, oPts: o, verdict: v, ctx: c })
  }

  const peg = n(f.peg_ratio)
  if (peg != null && peg > 0) {
    let r, o, v, c
    if      (peg > 3)   { r=3; o=0; v='high-risk';   c='Highly growth-adjusted expensive (PEG>3). Paying 3× the growth rate — historically associated with poor forward 3-year returns and high drawdown risk.' }
    else if (peg > 2)   { r=2; o=0; v='caution';     c='Growth premium elevated (PEG 2-3). Risk increases significantly if growth disappoints even modestly. Earnings revisions could be sharp.' }
    else if (peg > 1.2) { r=1; o=1; v='neutral';     c='Moderate growth premium (PEG 1.2-2). Acceptable for quality compounders with predictable earnings trajectories.' }
    else if (peg > 0.5) { r=0; o=2; v='opportunity'; c='GARP zone (PEG 0.5-1.2). Growth at a reasonable price — strong risk/reward for growth-oriented portfolios.' }
    else                { r=0; o=3; v='opportunity'; c='Deep growth value (PEG<0.5). Growth significantly underpriced. Primary opportunity: earnings re-rating as growth re-accelerates.' }
    valItems.push({ metric: 'PEG Ratio', value: peg.toFixed(2), rPts: r, oPts: o, verdict: v, ctx: c })
  }

  const fpe = n(f.forward_pe)
  if (fpe != null && pe != null && pe > 0 && fpe > 0) {
    const diff = pe - fpe
    let r, o, v, c
    if      (diff > 10) { r=0; o=3; v='opportunity'; c='Strong earnings expansion: forward P/E ('+fpe.toFixed(1)+'×) well below trailing ('+pe.toFixed(1)+'×). Growing into the multiple — reduces valuation risk over time.' }
    else if (diff > 3)  { r=0; o=2; v='opportunity'; c='Positive earnings momentum: forward P/E ('+fpe.toFixed(1)+'×) below trailing ('+pe.toFixed(1)+'×). Gradual multiple compression expected as earnings grow.' }
    else if (diff > -3) { r=1; o=1; v='neutral';     c='Flat earnings: forward P/E ('+fpe.toFixed(1)+'×) in line with trailing ('+pe.toFixed(1)+'×). Growth must re-accelerate to sustain current multiples.' }
    else                { r=3; o=0; v='high-risk';   c='Earnings expected to DECLINE: forward P/E ('+fpe.toFixed(1)+'×) ABOVE trailing ('+pe.toFixed(1)+'×). Deteriorating EPS at premium multiples is a high-risk configuration.' }
    valItems.push({ metric: 'Fwd vs TTM P/E', value: pe.toFixed(1)+'× → '+fpe.toFixed(1)+'×', rPts: r, oPts: o, verdict: v, ctx: c })
  }

  // ── TECHNICAL ────────────────────────────────────
  const rsi = n(i.rsi); const zone = (i.rsi_zone || '').toLowerCase()
  if (rsi != null) {
    let r, o, v, c
    if      (zone === 'overbought') { r=3; o=0; v='caution';     c='RSI '+rsi.toFixed(1)+' — overbought (>70). Entry risk is elevated. Near-term pullback probability is high. Favours waiting for RSI to cool or using smaller position sizes.' }
    else if (rsi > 60)              { r=1; o=2; v='opportunity'; c='RSI '+rsi.toFixed(1)+' — bullish momentum zone (60-70). Healthy trending territory, not yet overextended. Good risk/reward for trend-following entries.' }
    else if (rsi > 45)              { r=0; o=1; v='neutral';     c='RSI '+rsi.toFixed(1)+' — neutral (45-60). No strong directional edge. Favours systematic accumulation over momentum entry.' }
    else if (rsi > 30)              { r=2; o=2; v='neutral';     c='RSI '+rsi.toFixed(1)+' — weakening (30-45). Bearish short term but approaching mean-reversion zone. Wait for RSI to stabilise above 40 before acting on the opportunity.' }
    else                            { r=2; o=3; v='opportunity'; c='RSI '+rsi.toFixed(1)+' — oversold (<30). High mean-reversion opportunity potential. Risk: oversold can persist in downtrends. Require fundamental support before entering.' }
    techItems.push({ metric: 'RSI (14)', value: rsi.toFixed(1), rPts: r, oPts: o, verdict: v, ctx: c })
  }

  const macdState = (i.macd_state || '').toLowerCase()
  if (macdState) {
    const bull = macdState.includes('bull')
    techItems.push({ metric: 'MACD Momentum', value: bull ? 'Bullish' : 'Bearish',
      rPts: bull ? 0 : 2, oPts: bull ? 2 : 0,
      verdict: bull ? 'opportunity' : 'caution',
      ctx: bull
        ? 'MACD bullish configuration: positive momentum with lower near-term correction risk. Favours trend-following strategies and supports holding existing positions.'
        : 'MACD bearish: negative momentum. Higher near-term correction risk. Reduces attractiveness of immediate entries — wait for a momentum confirmation (histogram turning positive) before adding.'
    })
  }

  const trend = (i.trend || '').toLowerCase()
  if (trend) {
    const golden = trend.includes('golden')
    techItems.push({ metric: 'Trend Structure', value: golden ? 'Golden Cross' : 'Death Cross',
      rPts: golden ? 0 : 3, oPts: golden ? 2 : 0,
      verdict: golden ? 'opportunity' : 'high-risk',
      ctx: golden
        ? 'Golden cross (50d > 200d): long-term bullish structure intact. Institutional flows historically favour stocks in golden cross configurations. Lower structural drawdown risk.'
        : 'Death cross (50d < 200d): long-term bearish structure. Significantly elevated drawdown risk. Institutional selling pressure is likely until the 50-day recaptures the 200-day.'
    })
  }

  const hi52 = n(q.fifty_two_week_high), lo52 = n(q.fifty_two_week_low), price = n(q.price)
  if (hi52 && lo52 && price && hi52 > lo52) {
    const pctR = (price - lo52) / (hi52 - lo52) * 100
    let r, o, v, c
    if      (pctR >= 90) { r=3; o=0; v='caution';     c='Near 52-week high ('+pctR.toFixed(0)+'th percentile). Late-cycle entry risk. Limited upside to historical resistance. Favourable only if breaking to new highs on strong volume.' }
    else if (pctR >= 60) { r=1; o=2; v='opportunity'; c='Upper-middle of range ('+pctR.toFixed(0)+'th percentile). Constructive zone — stock has held gains without being at an extreme. Supports trend-following thesis.' }
    else if (pctR >= 30) { r=1; o=1; v='neutral';     c='Mid-range ('+pctR.toFixed(0)+'th percentile). Balanced entry — neither chasing highs nor catching a falling knife. Fundamentals are the primary driver.' }
    else                 { r=2; o=3; v='opportunity'; c='Near 52-week low ('+pctR.toFixed(0)+'th percentile). Contrarian opportunity zone. High reward if fundamentals are intact; high risk if the decline reflects permanent impairment.' }
    techItems.push({ metric: '52-Week Position', value: pctR.toFixed(0)+'th %ile', rPts: r, oPts: o, verdict: v, ctx: c })
  }

  // ── FUNDAMENTAL ──────────────────────────────────
  const rg = asPct(f.revenue_growth)
  if (rg != null) {
    let r, o, v, c
    if      (rg > 25)  { r=0; o=4; v='opportunity'; c='Strong revenue growth ('+rg.toFixed(0)+'% YoY). Top-line acceleration is the most reliable driver of long-term returns. Growing into current multiples reduces valuation risk organically.' }
    else if (rg > 10)  { r=0; o=2; v='opportunity'; c='Solid revenue growth ('+rg.toFixed(0)+'% YoY). Healthy expansion supporting current multiples and providing a buffer against multiple compression.' }
    else if (rg > 0)   { r=1; o=1; v='neutral';     c='Modest revenue growth ('+rg.toFixed(0)+'% YoY). Growing but slowly. At premium multiples this is a risk; at low multiples it may still represent value.' }
    else if (rg > -10) { r=3; o=0; v='caution';     c='Revenue declining ('+rg.toFixed(0)+'% YoY). Demand headwinds or structural challenges. Multiple compression risk is elevated unless the decline is clearly transient.' }
    else               { r=5; o=0; v='high-risk';   c='Significant revenue contraction ('+rg.toFixed(0)+'% YoY). Major red flag. Severe declines create compounding operational leverage and balance sheet stress.' }
    fundItems.push({ metric: 'Revenue Growth', value: (rg >= 0 ? '+' : '')+rg.toFixed(1)+'%', rPts: r, oPts: o, verdict: v, ctx: c })
  }

  const pm = asPct(f.profit_margin)
  if (pm != null) {
    let r, o, v, c
    if      (pm > 20) { r=0; o=3; v='opportunity'; c='High-quality business (margin '+pm.toFixed(1)+'%). Strong margins provide earnings stability, pricing power, and a cushion against revenue shocks. Key quality moat indicator.' }
    else if (pm > 10) { r=0; o=2; v='opportunity'; c='Solid margins ('+pm.toFixed(1)+'%). Healthy profitability that can fund growth and weather downturns. Supports the fundamental quality of the investment.' }
    else if (pm > 5)  { r=1; o=1; v='neutral';     c='Thin margins ('+pm.toFixed(1)+'%). Operational leverage cuts both ways — cost inflation or revenue miss can quickly erode profitability.' }
    else if (pm > 0)  { r=2; o=0; v='caution';     c='Very thin margins ('+pm.toFixed(1)+'%). Minimal buffer against headwinds. One bad quarter can erase annual profits and trigger sell-side downgrades.' }
    else              { r=4; o=0; v='high-risk';   c='Negative margins ('+pm.toFixed(1)+'%). Cash-burning business. Risk depends on burn rate, cash runway, and credible path to profitability.' }
    fundItems.push({ metric: 'Net Profit Margin', value: pm.toFixed(1)+'%', rPts: r, oPts: o, verdict: v, ctx: c })
  }

  const beta = n(f.beta)
  if (beta != null) {
    let r, o, v, c
    if      (beta > 2)   { r=4; o=0; v='high-risk';   c='High beta ('+beta.toFixed(2)+'). 2×+ market volatility. Amplifies both gains and losses. Requires smaller position sizes, tighter stops, and disciplined risk management.' }
    else if (beta > 1.5) { r=2; o=1; v='caution';     c='Elevated beta ('+beta.toFixed(2)+'). Significantly more volatile than the market. Suitable for higher risk-tolerance portfolios with conservatively sized positions.' }
    else if (beta > 1.1) { r=1; o=1; v='neutral';     c='Moderate beta ('+beta.toFixed(2)+'). Slightly above market volatility. Standard equity risk applies — no unusual volatility concerns.' }
    else if (beta > 0.7) { r=0; o=2; v='opportunity'; c='Market-like volatility (beta '+beta.toFixed(2)+'). Moves broadly with the index. Lower idiosyncratic risk than average individual stocks.' }
    else                 { r=0; o=2; v='opportunity'; c='Low-beta, defensive (beta '+beta.toFixed(2)+'). Less sensitive to market swings. Provides portfolio stability. Particularly valuable in uncertain macro environments.' }
    fundItems.push({ metric: 'Beta (Volatility)', value: beta.toFixed(2), rPts: r, oPts: o, verdict: v, ctx: c })
  }

  const eg = asPct(f.earnings_growth)
  if (eg != null) {
    let r, o, v, c
    if      (eg > 20) { r=0; o=3; v='opportunity'; c='Strong earnings growth ('+eg.toFixed(0)+'% YoY). Accelerating EPS is the ultimate long-term share price driver. Supports or expands current multiples.' }
    else if (eg > 5)  { r=0; o=2; v='opportunity'; c='Solid earnings growth ('+eg.toFixed(0)+'% YoY). Consistent EPS expansion reduces multiple compression risk and validates the valuation thesis.' }
    else if (eg > 0)  { r=1; o=1; v='neutral';     c='Flat earnings ('+eg.toFixed(0)+'% YoY). Growing but barely. At elevated multiples this creates valuation risk unless revenue re-accelerates.' }
    else              { r=4; o=0; v='high-risk';   c='Earnings declining ('+eg.toFixed(0)+'% YoY). Contracting EPS at premium multiples is a high-risk combination. Multiple compression is likely without a clear reversal catalyst.' }
    fundItems.push({ metric: 'Earnings Growth', value: (eg >= 0 ? '+' : '')+eg.toFixed(1)+'%', rPts: r, oPts: o, verdict: v, ctx: c })
  }

  // ── SENTIMENT ────────────────────────────────────
  const upside = n(a.target_upside_pct)
  if (upside != null) {
    let r, o, v, c
    if      (upside > 30) { r=0; o=4; v='opportunity'; c='Analysts see '+upside.toFixed(0)+'% upside to mean target. Strong consensus the stock is undervalued. Catalysts: earnings beats, multiple expansion, or positive news flow.' }
    else if (upside > 15) { r=0; o=2; v='opportunity'; c='Analysts see '+upside.toFixed(0)+'% upside. Exceeds typical annual market returns if achieved. Constructive risk/reward from the consensus view.' }
    else if (upside > 5)  { r=1; o=1; v='neutral';     c='Analysts see '+upside.toFixed(0)+'% upside. Limited cushion above consensus — requires a positive earnings surprise to outperform meaningfully.' }
    else if (upside > -5) { r=2; o=0; v='caution';     c='Price near analyst mean target ('+upside.toFixed(0)+'%). Limited upside headroom. Risk of downgrades if next earnings disappoints the street.' }
    else                  { r=4; o=0; v='high-risk';   c='Price EXCEEDS analyst target by '+Math.abs(upside).toFixed(0)+'%. Trading above consensus fair value. Elevated risk of target cuts and downward earnings revisions.' }
    sentItems.push({ metric: 'Analyst Target Upside', value: (upside >= 0 ? '+' : '')+upside.toFixed(1)+'%', rPts: r, oPts: o, verdict: v, ctx: c })
  }

  const recMean = n(a.recommendation_mean)
  if (recMean != null) {
    let r, o, v, c
    if      (recMean <= 1.5) { r=0; o=3; v='opportunity'; c='Strong buy consensus ('+recMean.toFixed(1)+'/5). Broad agreement on attractive risk/reward. Caution: monitor for crowded positioning in widely-followed names.' }
    else if (recMean <= 2.2) { r=0; o=2; v='opportunity'; c='Buy-leaning consensus ('+recMean.toFixed(1)+'/5). Majority of analysts see opportunity. Asymmetric risk/reward is tilted constructively.' }
    else if (recMean <= 3.0) { r=1; o=1; v='neutral';     c='Hold consensus ('+recMean.toFixed(1)+'/5). Analysts are mixed. Stock appears fairly priced. Company-specific execution will drive differentiated outcomes.' }
    else if (recMean <= 3.8) { r=2; o=0; v='caution';     c='Sell-leaning consensus ('+recMean.toFixed(1)+'/5). More analysts recommend reducing than buying. May reflect structural valuation or business model concerns.' }
    else                     { r=3; o=0; v='high-risk';   c='Strong sell consensus ('+recMean.toFixed(1)+'/5). Broad analyst agreement on downside. Rarely a contrarian buy — usually reflects real fundamental problems.' }
    sentItems.push({ metric: 'Analyst Consensus', value: recMean.toFixed(1)+' / 5', rPts: r, oPts: o, verdict: v, ctx: c })
  }

  // ── NORMALIZE 0-10 ───────────────────────────────
  const sumR = xs => xs.reduce((s, x) => s + x.rPts, 0)
  const sumO = xs => xs.reduce((s, x) => s + x.oPts, 0)
  const norm = (v, max) => max > 0 ? Math.min(10, (v / max) * 10) : 0

  const cats = {
    valuation:   { risk: norm(sumR(valItems),  11), opp: norm(sumO(valItems),  9),  items: valItems  },
    technical:   { risk: norm(sumR(techItems), 11), opp: norm(sumO(techItems), 9),  items: techItems },
    fundamental: { risk: norm(sumR(fundItems), 16), opp: norm(sumO(fundItems), 12), items: fundItems },
    sentiment:   { risk: norm(sumR(sentItems),  7), opp: norm(sumO(sentItems),  7), items: sentItems },
  }

  const allItems = [...valItems, ...techItems, ...fundItems, ...sentItems]
  const totalR = norm(sumR(allItems), 45)
  const totalO = norm(sumO(allItems), 37)
  const ratio  = totalR > 0.1 ? (totalO / totalR).toFixed(2) : '∞'

  let label, verdict
  if      (totalO >= 7 && totalR <= 3) { verdict = 'opportunity'; label = 'STRONG OPPORTUNITY' }
  else if (totalO >= 5 && totalR <= 5) { verdict = 'opportunity'; label = 'MODERATE OPPORTUNITY' }
  else if (totalR >= 7 && totalO <= 3) { verdict = 'high-risk';   label = 'HIGH RISK / LOW OPP' }
  else if (totalR >= 5)                { verdict = 'caution';     label = 'ELEVATED RISK' }
  else                                 { verdict = 'neutral';     label = 'BALANCED R/R' }

  return { overall: { risk: totalR, opp: totalO, ratio, label, verdict }, ...cats }
}

/* ── verdict helpers ── */
const VDCT_C = { opportunity: '#2dd4a0', neutral: '#e6a72a', caution: '#f97316', 'high-risk': '#f16a6a' }
const vc = v => VDCT_C[v] || '#5e6573'

const VerBadge = ({ verdict, label }) => {
  const c = vc(verdict)
  const lbl = label || { opportunity: 'OPPORTUNITY', neutral: 'NEUTRAL', caution: 'CAUTION', 'high-risk': 'HIGH RISK' }[verdict] || verdict
  return (
    <span style={{ fontSize: 10, fontFamily: T.mono, fontWeight: 700, color: c, background: c + '1a',
      border: `1px solid ${c}44`, padding: '1px 7px', borderRadius: 3, letterSpacing: '0.04em',
      whiteSpace: 'nowrap', textTransform: 'uppercase' }}>{lbl}</span>
  )
}

/* dual-axis bar — green from left (opportunity), red from right (risk) */
const DualBar = ({ label, risk, opp }) => (
  <div>
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, alignItems: 'center' }}>
      <span style={{ fontSize: 11, color: T.muted, fontFamily: T.mono }}>{label}</span>
      <span style={{ fontSize: 10, fontFamily: T.mono }}>
        <span style={{ color: T.green }}>OPP {opp.toFixed(1)}</span>
        <span style={{ color: T.dim }}> · </span>
        <span style={{ color: T.red }}>RISK {risk.toFixed(1)}</span>
      </span>
    </div>
    <div style={{ height: 5, background: T.surface, borderRadius: 4, position: 'relative', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: `${opp * 10}%`, background: T.green, opacity: 0.7, borderRadius: 4 }} />
      <div style={{ position: 'absolute', right: 0, top: 0, height: '100%', width: `${risk * 10}%`, background: T.red, opacity: 0.7, borderRadius: 4 }} />
    </div>
  </div>
)

/* mini risk/opp scorecard — used in Overview and RiskOppTab */
const RiskOppDashboard = ({ ror }) => {
  const c = vc(ror.overall.verdict)
  const cats = [
    { key: 'valuation',   label: 'Valuation'   },
    { key: 'technical',   label: 'Technical'   },
    { key: 'fundamental', label: 'Fundamental' },
    { key: 'sentiment',   label: 'Sentiment'   },
  ]
  return (
    <div style={{ border: `1px solid ${c}55`, background: c + '09', borderRadius: 8, padding: '12px 14px' }}>
      <div className="aa-ror-header">
        <div>
          <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 5 }}>RISK / OPPORTUNITY</div>
          <VerBadge verdict={ror.overall.verdict} label={ror.overall.label} />
        </div>
        <div className="aa-ror-scores">
          {[
            { lbl: 'OPPORTUNITY', val: ror.overall.opp, clr: T.green },
            { lbl: 'RISK',        val: ror.overall.risk, clr: T.red  },
            { lbl: 'R/R RATIO',   val: ror.overall.ratio, clr: c     },
          ].map(({ lbl, val, clr }) => (
            <div key={lbl}>
              <div style={{ fontSize: 9, color: T.muted, fontFamily: T.mono, marginBottom: 2 }}>{lbl}</div>
              <div style={{ fontSize: 18, fontFamily: T.mono, fontWeight: 700, color: clr, lineHeight: 1 }}>
                {typeof val === 'number' ? val.toFixed(1) : val}
                {typeof val === 'number' && <span style={{ fontSize: 10, color: T.dim }}>/10</span>}
              </div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ display: 'grid', gap: 8 }}>
        {cats.map(({ key, label }) => <DualBar key={key} label={label} risk={ror[key].risk} opp={ror[key].opp} />)}
      </div>
    </div>
  )
}

/* expandable metric row with risk/opp scores */
const RiskMetricRow = ({ metric, value, rPts, oPts, verdict, ctx }) => {
  const [open, setOpen] = useState(false)
  const c = vc(verdict)
  return (
    <div style={{ borderBottom: `1px solid ${T.border}` }}>
      <div onClick={() => setOpen(o => !o)}
           style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 14px', cursor: 'pointer' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 12, color: T.muted, fontFamily: T.mono }}>{metric}</span>
          <VerBadge verdict={verdict} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 10, fontFamily: T.mono, color: T.green }}>+{oPts} OPP</span>
          <span style={{ fontSize: 10, fontFamily: T.mono, color: T.red }}>−{rPts} RISK</span>
          <span style={{ fontSize: 13, fontFamily: T.mono, fontWeight: 700, color: T.text }}>{value}</span>
          <span style={{ fontSize: 10, color: T.dim }}>{open ? '▲' : '▼'}</span>
        </div>
      </div>
      {open && (
        <div style={{ padding: '0 14px 12px', borderTop: `1px solid ${T.border}` }}>
          <div style={{ display: 'flex', gap: 14, margin: '8px 0 6px' }}>
            <span style={{ fontSize: 10, fontFamily: T.mono, color: T.green, background: T.green + '18', border: `1px solid ${T.green}33`, padding: '2px 8px', borderRadius: 3 }}>
              +{oPts} Opportunity pts
            </span>
            <span style={{ fontSize: 10, fontFamily: T.mono, color: T.red, background: T.red + '18', border: `1px solid ${T.red}33`, padding: '2px 8px', borderRadius: 3 }}>
              −{rPts} Risk pts
            </span>
          </div>
          <div style={{ fontSize: 12, lineHeight: 1.7, color: T.muted, fontFamily: T.mono }}>{ctx}</div>
        </div>
      )}
    </div>
  )
}

/* expandable fundamentals row with inline verdict */
const FundRow = ({ m, v, ann }) => {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ borderBottom: `1px solid ${T.border}` }}>
      <div onClick={() => ann && setOpen(o => !o)}
           style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
             padding: '8px 13px', cursor: ann ? 'pointer' : 'default' }}>
        <span style={{ fontSize: 12, color: T.muted, fontFamily: T.mono }}>{m}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {ann && <VerBadge verdict={ann.verdict} />}
          <span style={{ fontSize: 13, color: T.text, fontFamily: T.mono, fontWeight: 700 }}>{v}</span>
          {ann && <span style={{ fontSize: 10, color: T.dim }}>{open ? '▲' : '▼'}</span>}
        </div>
      </div>
      {ann && open && (
        <div style={{ padding: '0 13px 10px', borderTop: `1px solid ${T.border}` }}>
          <div style={{ display: 'flex', gap: 10, margin: '6px 0 6px' }}>
            <span style={{ fontSize: 10, fontFamily: T.mono, color: T.green }}>+{ann.oPts} opportunity pts</span>
            <span style={{ fontSize: 10, fontFamily: T.mono, color: T.red }}>−{ann.rPts} risk pts</span>
          </div>
          <div style={{ fontSize: 11, lineHeight: 1.65, color: T.dim, fontFamily: T.mono }}>{ann.ctx}</div>
        </div>
      )}
    </div>
  )
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
  const ror = computeROR(data)

  // 52-week position gauge
  const hi52 = num(quote.fifty_two_week_high), lo52 = num(quote.fifty_two_week_low), px = num(quote.price)
  const pctRange = (hi52 && lo52 && px && hi52 > lo52) ? ((px - lo52) / (hi52 - lo52) * 100) : null

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {/* Risk / Opportunity Dashboard */}
      <RiskOppDashboard ror={ror} />

      {/* KPIs */}
      <div className="aa-kpi-grid">
        <KPI label="MARKET CAP" value={fmtCap(f.market_cap, cur)} />
        <KPI label="P/E (TTM)" value={fmtMult(f.trailing_pe)} />
        <KPI label="FORWARD P/E" value={fmtMult(f.forward_pe)} />
        <KPI label="BETA" value={fmt(f.beta)} />
        <KPI label="52W HIGH" value={fmtMoney(quote.fifty_two_week_high, cur)} />
        <KPI label="52W LOW" value={fmtMoney(quote.fifty_two_week_low, cur)} />
      </div>

      {/* 52-week range gauge */}
      {pctRange != null && (
        <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: '12px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <span style={{ fontSize: 10, color: T.muted, fontFamily: T.mono }}>52-WEEK PRICE RANGE</span>
            <span style={{ fontSize: 11, fontFamily: T.mono, color: T.amber }}>
              {pctRange.toFixed(0)}th percentile — {pctRange >= 90 ? 'Near high · Entry risk' : pctRange >= 60 ? 'Upper range · Constructive' : pctRange >= 30 ? 'Mid range · Balanced' : 'Near low · Contrarian opp'}
            </span>
          </div>
          <div style={{ height: 8, background: T.bg, borderRadius: 10, position: 'relative' }}>
            <div style={{ position: 'absolute', left: 0, height: '100%', width: '100%', borderRadius: 10,
              background: `linear-gradient(to right, ${T.green}60, ${T.amber}60, ${T.red}60)` }} />
            <div style={{ position: 'absolute', left: `${Math.min(97, Math.max(3, pctRange))}%`, top: '-3px', transform: 'translateX(-50%)',
              width: 14, height: 14, borderRadius: '50%', background: T.amber, border: `2px solid ${T.bg}` }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 5, fontSize: 10, fontFamily: T.mono, color: T.dim }}>
            <span>52W Low {fmtMoney(quote.fifty_two_week_low, cur)}</span>
            <span>52W High {fmtMoney(quote.fifty_two_week_high, cur)}</span>
          </div>
        </div>
      )}

      <Panel title={`Price — last ${chart.length} sessions`}>
        <div style={{ padding: 10 }}><PriceChart data={chart} cur={cur} showSMA={showSMA} showBB={false} /></div>
      </Panel>
      <div className="aa-two-col">
        <Panel title="Snapshot">
          <Row m="Sector" v={meta.sector || '—'} />
          <Row m="Industry" v={meta.industry || '—'} />
          <Row m="Trend" v={ind?.trend ? ind.trend.split(' ')[0] : '—'} />
          <Row m="RSI (14)" v={fmt(ind?.rsi, 1)} s={ind?.rsi_zone === 'overbought' ? 'sell' : ind?.rsi_zone === 'oversold' ? 'buy' : null} />
          <Row m="Dividend Yield" v={f.dividend_yield != null ? fmtFrac(f.dividend_yield) : '—'} />
        </Panel>
        <Panel title="Analyst Consensus">
          {coverageInfo(a.num_analysts).note && (
            <div style={{ padding: '10px 13px', borderBottom: `1px solid ${T.border}` }}>
              <CoverageBanner numAnalysts={a.num_analysts} compact />
            </div>
          )}
          <Row m="Recommendation" v={a.recommendation && a.recommendation !== 'none' ? a.recommendation.toUpperCase() : (num(a.num_analysts) ? '—' : 'NOT COVERED')} />
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
  const f   = data.fundamentals
  const cur = data.meta.currency
  const ror = computeROR(data)

  // Build annotation lookup by metric label
  const annMap = {}
  ;[...ror.valuation.items, ...ror.fundamental.items].forEach(x => { annMap[x.metric] = x })

  const rows = [
    { m: 'Market Cap',           v: fmtCap(f.market_cap, cur) },
    { m: 'Trailing P/E',         v: fmtMult(f.trailing_pe),     ann: annMap['Trailing P/E'] },
    { m: 'Forward P/E',          v: fmtMult(f.forward_pe),      ann: annMap['Fwd vs TTM P/E'] },
    { m: 'PEG Ratio',            v: fmt(f.peg_ratio),           ann: annMap['PEG Ratio'] },
    { m: 'Beta',                 v: fmt(f.beta),                ann: annMap['Beta (Volatility)'] },
    { m: 'Profit Margin',        v: fmtFrac(f.profit_margin),   ann: annMap['Net Profit Margin'] },
    { m: 'Revenue Growth (YoY)', v: fmtFrac(f.revenue_growth),  ann: annMap['Revenue Growth'] },
    { m: 'Earnings Growth (YoY)',v: fmtFrac(f.earnings_growth), ann: annMap['Earnings Growth'] },
    { m: 'Dividend Yield',       v: f.dividend_yield != null ? fmtFrac(f.dividend_yield) : '—' },
  ]

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {/* Mini valuation R/O bars */}
      <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: '12px 16px' }}>
        <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 10 }}>FUNDAMENTAL RISK / OPPORTUNITY (click any row below to expand context)</div>
        <div style={{ display: 'grid', gap: 8 }}>
          <DualBar label="Valuation" risk={ror.valuation.risk} opp={ror.valuation.opp} />
          <DualBar label="Fundamental Quality" risk={ror.fundamental.risk} opp={ror.fundamental.opp} />
        </div>
      </div>
      <Panel title="Fundamentals — click any row for risk/opportunity context">
        {rows.map(({ m, v, ann }) => <FundRow key={m} m={m} v={v} ann={ann} />)}
        <div style={{ padding: '8px 13px', fontSize: 10, color: T.dim, fontFamily: T.mono }}>
          Source: {data.data_source}. Fields shown as "—" are not provided by the free data feed.
        </div>
      </Panel>
    </div>
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
      <div className="aa-assess-kpis">
        <KPI label="SIGNAL" value={(a.signal || '—').toUpperCase()} />
        <KPI label="CONVICTION" value={(a.conviction || '—').toUpperCase()} />
        <KPI label="RISK LEVEL" value={(a.risk_level || '—').toUpperCase()} />
        <KPI label="HORIZON" value={a.time_horizon || '—'} />
      </div>
      <Panel title="Summary"><div style={{ padding: 14, fontSize: 13, color: T.text }}><MarkdownLite text={a.summary} /></div></Panel>
      <div className="aa-two-col">
        <Panel title="Bullish Factors">
          {(a.bullish_factors || []).map((x, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '8px 13px', fontSize: 12, color: T.text, borderBottom: `1px solid ${T.border}` }}>
              <span style={{ color: T.green, flexShrink: 0 }}>↗</span><span style={{ flex: 1 }}><MarkdownLite text={x} /></span>
            </div>
          ))}
        </Panel>
        <Panel title="Bearish Factors">
          {(a.bearish_factors || []).map((x, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '8px 13px', fontSize: 12, color: T.text, borderBottom: `1px solid ${T.border}` }}>
              <span style={{ color: T.red, flexShrink: 0 }}>↘</span><span style={{ flex: 1 }}><MarkdownLite text={x} /></span>
            </div>
          ))}
        </Panel>
      </div>
      {a.technical_read && <Panel title="Technical Read"><div style={{ padding: 14, fontSize: 13, color: T.text }}><MarkdownLite text={a.technical_read} /></div></Panel>}
      {a.reasoning && <Panel title="Reasoning"><div style={{ padding: 14, fontSize: 13, color: T.text }}><MarkdownLite text={a.reasoning} /></div></Panel>}
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
            {peers.map((p, i) => {
              const pc = p.currency || data.meta.currency
              return (
              <tr key={i} style={{ borderBottom: `1px solid ${T.border}`, background: p.ticker === data.meta.ticker ? T.amberDim : 'transparent' }}>
                <td style={{ padding: '9px 13px', fontSize: 13, fontFamily: T.mono, color: T.amber, fontWeight: 700 }}>{p.ticker}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, color: T.text, whiteSpace: 'nowrap' }}>{p.name}</td>
                <td style={{ padding: '9px 13px', fontSize: 13, fontFamily: T.mono, color: T.text, fontWeight: 700, whiteSpace: 'nowrap' }}>{p.price != null ? `${curSym(pc)}${fmt(p.price)}` : '—'}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, fontFamily: T.mono, fontWeight: 700, color: num(p.change_pct) >= 0 ? T.green : T.red }}>{fmtPct(p.change_pct)}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, fontFamily: T.mono, color: T.muted, whiteSpace: 'nowrap' }}>{fmtCap(p.market_cap, pc)}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, fontFamily: T.mono, color: T.muted }}>{fmtMult(p.trailing_pe)}</td>
                <td style={{ padding: '9px 13px', fontSize: 12, fontFamily: T.mono, color: T.muted }}>{fmtFrac(p.profit_margin)}</td>
              </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function RiskOppTab({ data }) {
  const ror = computeROR(data)
  const [section, setSection] = useState('all')

  const cats = [
    { key: 'all',        label: 'ALL METRICS' },
    { key: 'valuation',  label: 'VALUATION'   },
    { key: 'technical',  label: 'TECHNICAL'   },
    { key: 'fundamental',label: 'FUNDAMENTAL' },
    { key: 'sentiment',  label: 'SENTIMENT'   },
  ]

  const activeItems = section === 'all'
    ? [...ror.valuation.items, ...ror.technical.items, ...ror.fundamental.items, ...ror.sentiment.items]
    : (ror[section]?.items || [])

  const catRisk = activeItems.reduce((s, x) => s + x.rPts, 0)
  const catOpp  = activeItems.reduce((s, x) => s + x.oPts, 0)
  const highRisk = activeItems.filter(x => x.verdict === 'high-risk')
  const opps     = activeItems.filter(x => x.verdict === 'opportunity')

  const showCovNote = coverageInfo(data.analyst?.num_analysts).note &&
    (section === 'all' || section === 'sentiment')

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {/* Full R/O Dashboard */}
      <RiskOppDashboard ror={ror} />

      {/* Low-coverage explainer (Sentiment scores depend on analyst data) */}
      {showCovNote && <CoverageBanner numAnalysts={data.analyst.num_analysts} />}

      {/* Section filter */}
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
        {cats.map(({ key, label }) => (
          <button key={key} onClick={() => setSection(key)} style={{
            fontSize: 10, fontFamily: T.mono, fontWeight: 700, cursor: 'pointer',
            border: `1px solid ${section === key ? T.amber : T.border}`,
            background: section === key ? T.amberDim : 'transparent',
            color: section === key ? T.amber : T.muted, padding: '4px 10px', borderRadius: 3,
          }}>{label}</button>
        ))}
      </div>

      {/* Key signal highlights */}
      {(highRisk.length > 0 || opps.length > 0) && (
        <div className="aa-two-col">
          {opps.length > 0 && (
            <div style={{ background: T.green + '0d', border: `1px solid ${T.green}33`, borderRadius: 8, padding: '12px 14px' }}>
              <div style={{ fontSize: 10, color: T.green, fontFamily: T.mono, fontWeight: 700, marginBottom: 8 }}>⬆ OPPORTUNITY SIGNALS ({opps.length})</div>
              {opps.map((x, idx) => (
                <div key={idx} style={{ fontSize: 12, color: T.text, fontFamily: T.mono, padding: '3px 0', borderBottom: idx < opps.length - 1 ? `1px solid ${T.green}22` : 'none' }}>
                  <span style={{ color: T.green, marginRight: 6 }}>+{x.oPts}</span>{x.metric} — {x.value}
                </div>
              ))}
            </div>
          )}
          {highRisk.length > 0 && (
            <div style={{ background: T.red + '0d', border: `1px solid ${T.red}33`, borderRadius: 8, padding: '12px 14px' }}>
              <div style={{ fontSize: 10, color: T.red, fontFamily: T.mono, fontWeight: 700, marginBottom: 8 }}>⬇ HIGH RISK SIGNALS ({highRisk.length})</div>
              {highRisk.map((x, idx) => (
                <div key={idx} style={{ fontSize: 12, color: T.text, fontFamily: T.mono, padding: '3px 0', borderBottom: idx < highRisk.length - 1 ? `1px solid ${T.red}22` : 'none' }}>
                  <span style={{ color: T.red, marginRight: 6 }}>−{x.rPts}</span>{x.metric} — {x.value}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Score tally for active filter */}
      <div style={{ display: 'flex', gap: 16, padding: '8px 14px', background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 11, color: T.muted, fontFamily: T.mono }}>
          {activeItems.length} metrics · Click any row to expand context
        </span>
        <span style={{ fontSize: 11, fontFamily: T.mono, color: T.green, marginLeft: 'auto' }}>
          Total opportunity pts: {catOpp}
        </span>
        <span style={{ fontSize: 11, fontFamily: T.mono, color: T.red }}>
          Total risk pts: {catRisk}
        </span>
      </div>

      {/* Metric cards */}
      <Panel title={cats.find(c => c.key === section)?.label + ' — RISK / OPPORTUNITY BREAKDOWN'}>
        {activeItems.length === 0
          ? <div style={{ padding: 16, fontSize: 12, color: T.muted }}>No data available for this category.</div>
          : activeItems.map((item, idx) => <RiskMetricRow key={idx} {...item} />)
        }
      </Panel>

      <div style={{ fontSize: 10, color: T.dim, fontFamily: T.mono }}>
        Risk/Opportunity scores are quantitative signals computed from live market data for educational purposes only — not financial advice.
        Scores are relative indicators, not absolute forecasts. Past metrics do not guarantee future returns.
      </div>
    </div>
  )
}

/* ───────────── lightweight markdown renderer (no dependency) ─────────────
   Renders the LLM's markdown (**bold**, ### headers, * bullets, 1. lists,
   `code`) as styled React nodes instead of leaking raw asterisks/hashes. */
function mdInline(text, kp = '') {
  const nodes = []
  let rest = String(text)
  let k = 0
  // bold (**), inline code (`), italic (*), links [t](u)
  const re = /(\*\*([^*]+?)\*\*|`([^`]+?)`|\[([^\]]+)\]\((https?:\/\/[^)]+)\)|\*([^*\n]+?)\*)/
  let m
  while ((m = re.exec(rest))) {
    if (m.index > 0) nodes.push(rest.slice(0, m.index))
    if (m[2] != null) nodes.push(<strong key={kp + 'b' + k++} style={{ color: '#fff', fontWeight: 700 }}>{m[2]}</strong>)
    else if (m[3] != null) nodes.push(<code key={kp + 'c' + k++} style={{ fontFamily: T.mono, fontSize: '0.92em', background: 'rgba(255,255,255,0.07)', border: `1px solid ${T.border}`, borderRadius: 4, padding: '1px 5px', color: T.amber }}>{m[3]}</code>)
    else if (m[4] != null) nodes.push(<a key={kp + 'a' + k++} href={m[5]} target="_blank" rel="noreferrer" style={{ color: T.cyan, textDecoration: 'underline' }}>{m[4]}</a>)
    else if (m[6] != null) nodes.push(<em key={kp + 'i' + k++} style={{ color: T.text }}>{m[6]}</em>)
    rest = rest.slice(m.index + m[0].length)
  }
  if (rest) nodes.push(rest)
  return nodes
}

function MarkdownLite({ text }) {
  const lines = String(text || '').split('\n')
  const blocks = []
  let i = 0
  const HEAD = { 1: 17, 2: 15, 3: 13.5, 4: 12.5 }
  while (i < lines.length) {
    const line = lines[i]

    // ── header ── ### Title
    const h = line.match(/^\s*(#{1,4})\s+(.*)/)
    if (h) {
      const lvl = h[1].length
      blocks.push(
        <div key={'h' + i} style={{ fontSize: HEAD[lvl] || 13, fontWeight: 700, color: T.amber,
          fontFamily: T.head, letterSpacing: '0.01em', margin: blocks.length ? '12px 0 5px' : '0 0 5px' }}>
          {mdInline(h[2], 'h' + i)}
        </div>
      )
      i++; continue
    }

    // ── horizontal rule ──
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      blocks.push(<div key={'hr' + i} style={{ height: 1, background: T.border, margin: '10px 0' }} />)
      i++; continue
    }

    // ── bullet list ── * item  /  - item
    if (/^\s*[-*•]\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*•]\s+/, '')); i++
      }
      blocks.push(
        <div key={'ul' + i} style={{ display: 'grid', gap: 5, margin: '4px 0' }}>
          {items.map((it, j) => (
            <div key={j} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <span style={{ color: T.amber, flexShrink: 0, lineHeight: 1.6, fontSize: 11 }}>▸</span>
              <span style={{ flex: 1 }}>{mdInline(it, 'ul' + i + '_' + j)}</span>
            </div>
          ))}
        </div>
      )
      continue
    }

    // ── numbered list ── 1. item
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*(\d+)\.\s+/, '$1|')); i++
      }
      blocks.push(
        <div key={'ol' + i} style={{ display: 'grid', gap: 5, margin: '4px 0' }}>
          {items.map((it, j) => {
            const [n, ...r] = it.split('|')
            return (
              <div key={j} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <span style={{ color: T.amber, flexShrink: 0, lineHeight: 1.6, fontWeight: 700, minWidth: 16 }}>{n}.</span>
                <span style={{ flex: 1 }}>{mdInline(r.join('|'), 'ol' + i + '_' + j)}</span>
              </div>
            )
          })}
        </div>
      )
      continue
    }

    // ── blank line ──
    if (line.trim() === '') { i++; continue }

    // ── paragraph (gather consecutive plain lines) ──
    const para = []
    while (
      i < lines.length && lines[i].trim() !== '' &&
      !/^\s*(#{1,4})\s/.test(lines[i]) &&
      !/^\s*[-*•]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^\s*([-*_])\1{2,}\s*$/.test(lines[i])
    ) { para.push(lines[i]); i++ }
    blocks.push(
      <div key={'p' + i} style={{ margin: '4px 0', lineHeight: 1.7 }}>
        {para.map((l, j) => (
          <span key={j}>{mdInline(l, 'p' + i + '_' + j)}{j < para.length - 1 ? <br /> : null}</span>
        ))}
      </div>
    )
  }
  return <div>{blocks}</div>
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
    <div className="aa-chat">
      {!aiConfigured && (
        <div style={{ background: T.amberDim, border: `1px solid ${T.amberBorder}`, borderRadius: 6, padding: '8px 12px', fontSize: 12, color: T.amber, fontFamily: T.mono }}>
          AI chat needs an LLM key on the server. Without one, requests will return a 503.
        </div>
      )}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 14, paddingRight: 6 }}>
        {msgs.map((m, i) => {
          const isUser = m.role === 'user'
          return (
            <div key={i} style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', gap: 10 }}>
              {!isUser && (
                <div style={{ width: 30, height: 30, borderRadius: '50%', background: T.amberDim,
                  border: `1px solid ${T.amber}55`, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 14, flexShrink: 0, marginTop: 2 }}>⚡</div>
              )}
              <div style={{
                maxWidth: isUser ? '78%' : '86%',
                padding: isUser ? '10px 14px' : '12px 16px',
                borderRadius: isUser ? '12px 12px 4px 12px' : '4px 12px 12px 12px',
                fontSize: 13.5, color: T.text,
                background: isUser ? T.amberDim : T.surfaceRaised,
                border: `1px solid ${isUser ? T.amberBorder : T.border}`,
                fontFamily: isUser ? T.mono : T.head,
                whiteSpace: isUser ? 'pre-wrap' : 'normal',
                lineHeight: 1.7, wordBreak: 'break-word', overflowWrap: 'anywhere',
              }}>
                {isUser ? m.text : <MarkdownLite text={m.text} />}
              </div>
            </div>
          )
        })}
        {busy && (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <div style={{ width: 30, height: 30, borderRadius: '50%', background: T.amberDim, border: `1px solid ${T.amber}55`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, flexShrink: 0 }}>⚡</div>
            <div style={{ display: 'flex', gap: 4, padding: '12px 16px', background: T.surfaceRaised, border: `1px solid ${T.border}`, borderRadius: '4px 12px 12px 12px' }}>
              {[0, 1, 2].map(d => (
                <span key={d} style={{ width: 6, height: 6, borderRadius: '50%', background: T.amber, display: 'inline-block', animation: `aadot 1.2s ${d * 0.18}s infinite ease-in-out` }} />
              ))}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {msgs.length <= 1 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {SUGGEST.map((s, i) => (
            <button key={i} onClick={() => send(s)} style={{ fontSize: 11.5, fontFamily: T.head, color: T.text,
              background: T.surface, border: `1px solid ${T.borderMid}`, borderRadius: 20, padding: '6px 13px',
              cursor: 'pointer', transition: 'all .15s' }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = T.amberBorder; e.currentTarget.style.color = T.amber }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = T.borderMid; e.currentTarget.style.color = T.text }}>
              {s}
            </button>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
          placeholder={`Ask the analyst about ${ticker}...`}
          style={{ flex: 1, background: T.surface, border: `1px solid ${T.borderMid}`, borderRadius: 10, padding: '11px 15px', color: T.text, fontSize: 13.5, fontFamily: T.head, outline: 'none' }} />
        <button onClick={() => send()} disabled={busy || !input.trim()}
          style={{ background: busy || !input.trim() ? T.amberDim : T.amber, border: 'none', borderRadius: 10, padding: '0 22px', color: busy || !input.trim() ? T.amber : '#000', fontSize: 12, fontFamily: T.mono, fontWeight: 700, cursor: busy || !input.trim() ? 'not-allowed' : 'pointer', transition: 'all .15s' }}>SEND ↑</button>
      </div>
    </div>
  )
}

/* ───────────── hero bar ───────────── */
function HeroBar({ data, quote, pos }) {
  const ror = computeROR(data)
  const rc  = vc(ror.overall.verdict)
  return (
    <div className="aa-hero">
      {/* Ticker + company */}
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: T.head, fontWeight: 700, fontSize: 24, color: T.amber, lineHeight: 1 }}>{data.meta.ticker}</span>
          <span style={{ fontSize: 13, color: T.muted, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{data.meta.name}</span>
        </div>
        <div style={{ fontSize: 11, color: T.dim, fontFamily: T.mono, marginTop: 2 }}>{[data.meta.exchange, data.meta.sector].filter(Boolean).join(' · ')}</div>
      </div>
      {/* Price */}
      <div className="aa-hero-price">
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{ fontFamily: T.mono, fontWeight: 700, fontSize: 26, color: T.text }}>{fmtMoney(quote.price, data.meta.currency)}</span>
          <span style={{ fontFamily: T.mono, fontWeight: 700, fontSize: 14, color: pos ? T.green : T.red }}>{fmtPct(quote.day_change_pct)}</span>
        </div>
      </div>
      {/* R/R — on mobile becomes full-width row below price */}
      <div className="aa-hero-rr">
        <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 3 }}>RISK / REWARD</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <VerBadge verdict={ror.overall.verdict} label={ror.overall.label} />
          <span style={{ fontSize: 11, fontFamily: T.mono }}>
            <span style={{ color: T.green }}>OPP {ror.overall.opp.toFixed(1)}</span>
            <span style={{ color: T.dim }}> · </span>
            <span style={{ color: T.red }}>RISK {ror.overall.risk.toFixed(1)}</span>
            <span style={{ color: T.dim }}> · R/R </span>
            <span style={{ color: rc, fontWeight: 700 }}>{ror.overall.ratio}</span>
          </span>
        </div>
      </div>
      {/* Analyst consensus — on mobile becomes full-width row */}
      <div className="aa-hero-consensus">
        <div>
          <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 2, display: 'flex', alignItems: 'center', gap: 6 }}>
            ANALYST CONSENSUS <CoverageChip numAnalysts={data.analyst.num_analysts} />
          </div>
          <div style={{ fontFamily: T.head, fontWeight: 700, fontSize: 15,
            color: data.analyst.recommendation && data.analyst.recommendation !== 'none' ? T.green : T.muted }}>
            {data.analyst.recommendation && data.analyst.recommendation !== 'none'
              ? data.analyst.recommendation.toUpperCase()
              : (num(data.analyst.num_analysts) ? '—' : 'NOT COVERED')}
          </div>
        </div>
        <div style={{ fontSize: 11, color: T.muted, fontFamily: T.mono, marginTop: 1 }}>
          {fmt(data.analyst.num_analysts, 0)} analysts · PT {fmt(data.analyst.target_mean)}
        </div>
      </div>
    </div>
  )
}

/* ───────────── main app ───────────── */
const TABS = [
  { id: 'overview',     label: 'OVERVIEW'      },
  { id: 'risk',         label: '⚖ RISK / OPP'  },
  { id: 'fundamentals', label: 'FUNDAMENTALS'  },
  { id: 'technicals',   label: 'TECHNICALS'    },
  { id: 'charts',       label: 'CHARTS'        },
  { id: 'news',         label: 'NEWS'          },
  { id: 'peers',        label: 'PEERS'         },
  { id: 'assessment',   label: 'ASSESSMENT'    },
  { id: 'chat',         label: '⚡ AI ANALYST'  },
]

export default function App() {
  const [search, setSearch] = useState('')
  const [ticker, setTicker] = useState('')
  const [data, setData] = useState(null)
  const [chart, setChart] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('overview')

  // ── autocomplete state ──
  const [suggest, setSuggest] = useState([])
  const [showSuggest, setShowSuggest] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const suggestTimer = useRef(null)
  const suggestSeq = useRef(0)

  async function run(sym) {
    const s = (sym ?? search).trim().toUpperCase()
    if (!s) return
    setShowSuggest(false); setSuggest([])
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

  // Debounced live symbol search as the user types.
  function onSearchChange(val) {
    setSearch(val)
    setActiveIdx(-1)
    if (suggestTimer.current) clearTimeout(suggestTimer.current)
    const q = val.trim()
    if (q.length < 1) { setSuggest([]); setShowSuggest(false); return }
    const seq = ++suggestSeq.current
    suggestTimer.current = setTimeout(async () => {
      const results = await searchSymbols(q)
      // Ignore out-of-order responses (a later keystroke already fired).
      if (seq !== suggestSeq.current) return
      setSuggest(results)
      setShowSuggest(results.length > 0)
    }, 180)
  }

  function pickSuggestion(sym) {
    setSearch(sym)
    setShowSuggest(false)
    setSuggest([])
    run(sym)
  }

  function onSearchKeyDown(e) {
    if (showSuggest && suggest.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx(i => Math.min(i + 1, suggest.length - 1)); return }
      if (e.key === 'ArrowUp')   { e.preventDefault(); setActiveIdx(i => Math.max(i - 1, -1)); return }
      if (e.key === 'Escape')    { setShowSuggest(false); return }
      if (e.key === 'Enter' && activeIdx >= 0 && suggest[activeIdx]) {
        e.preventDefault(); pickSuggestion(suggest[activeIdx].symbol); return
      }
    }
    if (e.key === 'Enter') run()
  }

  // Self-heal missing metrics: if a result comes back with key fundamentals
  // blank (e.g. the data source briefly throttled), silently re-fetch a few
  // times in the background until they fill in — so the user rarely sees "—".
  const retryRef = useRef({ ticker: null, attempts: 0 })
  const activeTickerRef = useRef(null)
  useEffect(() => { activeTickerRef.current = data?.meta?.ticker || null }, [data])
  useEffect(() => {
    if (!data) return
    const tk = data.meta?.ticker
    if (retryRef.current.ticker !== tk) retryRef.current = { ticker: tk, attempts: 0 }
    const thin = data.fundamentals?.market_cap == null  // a key metric is missing
    if (!thin || retryRef.current.attempts >= 4) return
    retryRef.current.attempts += 1
    const delay = 3000 + retryRef.current.attempts * 2500  // 5.5s, 8s, 10.5s, 13s
    const id = setTimeout(async () => {
      try {
        const fresh = await analyzeTicker(tk)
        // Only apply if the user is still viewing this same stock.
        if (fresh?.meta?.ticker === tk && activeTickerRef.current === tk) {
          setData(fresh)
          setChart(buildSeries(fresh.series || {}))
        }
      } catch { /* ignore — will retry on the next tick if still thin */ }
    }, delay)
    return () => clearTimeout(id)
  }, [data])

  const quote = data?.quote
  const pos = num(quote?.day_change_pct) >= 0

  return (
    <div style={{ background: T.bg, minHeight: '100vh', color: T.text }}>
      <style>{`
        *{box-sizing:border-box;}
        body{overflow-x:hidden;}
        ::-webkit-scrollbar{width:4px;height:4px;}
        ::-webkit-scrollbar-track{background:${T.surface};}
        ::-webkit-scrollbar-thumb{background:#2d3748;border-radius:3px;}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
        @keyframes aadot{0%,60%,100%{transform:translateY(0);opacity:.4}30%{transform:translateY(-5px);opacity:1}}
        input::placeholder{color:${T.dim};}
        button{font-family:inherit;}
        /* ── layout classes ── */
        .aa-nav{background:${T.nav};border-bottom:1px solid ${T.border};padding:0 20px;height:50px;display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:100;}
        .aa-nav-brand{display:flex;align-items:center;gap:6px;flex-shrink:0;}
        .aa-nav-search{display:flex;gap:6px;flex:1;max-width:420px;}
        .aa-nav-status{margin-left:auto;display:flex;align-items:center;gap:6px;flex-shrink:0;}
        .aa-tabs{background:${T.nav};border-bottom:1px solid ${T.border};padding:0 20px;display:flex;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;}
        .aa-tabs::-webkit-scrollbar{display:none;}
        .aa-tabs button{padding:11px 14px;font-size:12px;font-family:${T.head};font-weight:700;cursor:pointer;border:none;outline:none;white-space:nowrap;background:transparent;flex-shrink:0;}
        .aa-content{padding:16px 20px 80px;max-width:1120px;}
        .aa-statusbar{background:#040408;border-top:1px solid ${T.amberBorder};padding:5px 20px;display:flex;align-items:center;position:fixed;bottom:0;left:0;right:0;gap:10px;z-index:90;}
        .aa-hero{background:${T.nav};border-bottom:1px solid ${T.border};padding:12px 20px;display:flex;align-items:center;gap:24px;flex-wrap:wrap;}
        .aa-hero-price{border-left:1px solid ${T.border};padding-left:24px;}
        .aa-hero-rr{border-left:1px solid ${T.border};padding-left:20px;}
        .aa-hero-consensus{margin-left:auto;text-align:right;}
        .aa-kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;}
        .aa-two-col{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;}
        .aa-assess-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;}
        .aa-ror-header{display:flex;align-items:center;gap:20px;flex-wrap:wrap;margin-bottom:14px;}
        .aa-ror-scores{display:flex;gap:20px;}
        .aa-chat{display:flex;flex-direction:column;height:540px;gap:10px;}
        /* ── mobile overrides ── */
        @media(max-width:600px){
          .aa-nav{padding:0 10px;gap:8px;}
          .aa-nav-brand{display:none;}
          .aa-nav-search{max-width:none;}
          .aa-nav-status-label{display:none;}
          .aa-tabs{padding:0 4px;}
          .aa-tabs button{padding:10px 10px;font-size:11px;}
          .aa-content{padding:10px 10px 76px;}
          .aa-statusbar{padding:4px 10px;}
          .aa-statusbar-disc{display:none;}
          .aa-hero{padding:10px 10px;gap:8px;}
          .aa-hero-price{border-left:none;padding-left:0;}
          .aa-hero-rr{border-left:none;padding-left:0;order:3;width:100%;}
          .aa-hero-consensus{margin-left:0;text-align:left;order:4;width:100%;display:flex;justify-content:space-between;align-items:center;border-top:1px solid ${T.border};padding-top:8px;}
          .aa-kpi-grid{grid-template-columns:repeat(2,1fr);}
          .aa-two-col{grid-template-columns:1fr;}
          .aa-assess-kpis{grid-template-columns:repeat(2,1fr);}
          .aa-ror-header{gap:10px;margin-bottom:10px;}
          .aa-ror-scores{gap:14px;flex-wrap:wrap;}
          .aa-ror-scores > div{min-width:60px;}
          .aa-chat{height:calc(100svh - 260px);min-height:320px;}
        }
      `}</style>

      {/* top nav */}
      <div className="aa-nav">
        <div className="aa-nav-brand">
          <span style={{ fontFamily: T.head, fontWeight: 700, fontSize: 18, color: T.amber }}>ANALYST</span>
          <span style={{ fontFamily: T.head, fontSize: 18, color: T.muted }}>AGENT</span>
        </div>
        <div className="aa-nav-search" style={{ position: 'relative' }}>
          <input value={search}
            onChange={e => onSearchChange(e.target.value)}
            onKeyDown={onSearchKeyDown}
            onFocus={() => suggest.length && setShowSuggest(true)}
            onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
            placeholder="Search any stock — name or ticker…"
            autoComplete="off" autoCorrect="off" autoCapitalize="characters" spellCheck="false"
            style={{ flex: 1, background: '#0f121a', border: `1px solid ${T.border}`, borderRadius: 6, padding: '7px 12px', color: T.text, fontSize: 13, fontFamily: T.mono, outline: 'none', minWidth: 0 }} />
          <button onClick={() => run()} disabled={loading}
            style={{ background: T.amberDim, border: `1px solid ${T.amberBorder}`, borderRadius: 6, padding: '7px 14px', color: T.amber, fontSize: 12, fontFamily: T.mono, fontWeight: 700, cursor: 'pointer', flexShrink: 0 }}>
            {loading ? '…' : 'GO'}
          </button>
          {showSuggest && suggest.length > 0 && (
            <div style={{ position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0,
              background: T.surface, border: `1px solid ${T.borderMid}`, borderRadius: 8,
              overflow: 'hidden', zIndex: 200, maxHeight: 360, overflowY: 'auto',
              boxShadow: '0 8px 28px rgba(0,0,0,0.55)' }}>
              {suggest.map((s, i) => (
                <div key={s.symbol + i}
                  onMouseDown={e => { e.preventDefault(); pickSuggestion(s.symbol) }}
                  onMouseEnter={() => setActiveIdx(i)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px',
                    cursor: 'pointer', borderBottom: i < suggest.length - 1 ? `1px solid ${T.border}` : 'none',
                    background: i === activeIdx ? T.amberDim : 'transparent' }}>
                  <span style={{ fontFamily: T.mono, fontWeight: 700, color: T.amber, fontSize: 12, minWidth: 58, flexShrink: 0 }}>{s.symbol}</span>
                  <span style={{ fontSize: 12, color: T.text, flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</span>
                  <span style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, whiteSpace: 'nowrap', flexShrink: 0 }}>{[s.exchange, s.type].filter(Boolean).join(' · ')}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="aa-nav-status">
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: T.green, animation: 'pulse 2s infinite', display: 'inline-block' }} />
          <span className="aa-nav-status-label" style={{ fontSize: 11, fontFamily: T.mono, color: T.muted }}>{data?.engine || 'ready'}</span>
        </div>
      </div>

      {/* hero bar */}
      {data && <HeroBar data={data} quote={quote} pos={pos} />}

      {/* tabs */}
      {data && (
        <div className="aa-tabs">
          {TABS.map(({ id, label }) => (
            <button key={id} onClick={() => setTab(id)}
              style={{ color: tab === id ? T.amber : T.muted,
                borderBottom: tab === id ? `2px solid ${T.amber}` : '2px solid transparent' }}>{label}</button>
          ))}
        </div>
      )}

      {/* content */}
      <div className="aa-content">
        {error && <div style={{ background: 'rgba(241,106,106,0.1)', border: `1px solid ${T.red}55`, borderRadius: 8, padding: '12px 16px', color: T.red, fontSize: 13, marginBottom: 10 }}>{error}</div>}
        {loading && !data && <div style={{ padding: 60, textAlign: 'center', color: T.muted }}>Analyzing {search || '…'}…</div>}
        {!data && !loading && !error && (
          <div style={{ padding: '60px 10px', textAlign: 'center', color: T.muted }}>
            <div style={{ fontSize: 15, marginBottom: 8 }}>Enter a ticker to begin.</div>
            <div style={{ fontSize: 12, color: T.dim }}>Try AAPL · MSFT · NVDA · TSLA · D05.SI · 1023.KL</div>
            <div style={{ fontSize: 11, color: T.dim, fontFamily: T.mono, marginTop: 28 }}>
              Created by <span style={{ color: T.muted, fontWeight: 700 }}>Hsien Hern Koay</span>
            </div>
          </div>
        )}
        {data && (
          <>
            {tab === 'overview'     && <OverviewTab data={data} chart={chart} />}
            {tab === 'risk'         && <RiskOppTab data={data} />}
            {tab === 'fundamentals' && <FundamentalsTab data={data} />}
            {tab === 'technicals'   && <TechnicalsTab data={data} />}
            {tab === 'charts'       && <ChartsTab data={data} chart={chart} />}
            {tab === 'news'         && <NewsTab data={data} />}
            {tab === 'peers'        && <PeersTab data={data} />}
            {tab === 'assessment'   && <AssessmentTab data={data} />}
            {tab === 'chat' && <ChatTab ticker={data.meta.ticker} aiConfigured={!!data.engine && data.engine !== 'rules'} />}
          </>
        )}
      </div>

      {/* status bar */}
      <div className="aa-statusbar">
        <span style={{ fontSize: 10, fontFamily: T.mono, color: T.amber }}>⚡ ANALYST AGENT</span>
        <span style={{ fontSize: 10, fontFamily: T.mono, color: T.muted }}>
          · Created by <span style={{ color: T.text, fontWeight: 700 }}>Hsien Hern Koay</span>
        </span>
        {data && <span className="aa-statusbar-disc" style={{ fontSize: 10, fontFamily: T.mono, color: T.dim }}>· {data.data_source}</span>}
        <span className="aa-statusbar-disc" style={{ marginLeft: 'auto', fontSize: 10, fontFamily: T.mono, color: T.dim }}>Educational only · Not financial advice</span>
      </div>
    </div>
  )
}
