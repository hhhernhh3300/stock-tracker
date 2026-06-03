import { useState, useRef, useEffect } from 'react'
import {
  ComposedChart, AreaChart, BarChart, LineChart,
  Area, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts'
import { analyzeTicker } from './api'

/* ─── Design Tokens ────────────────────────────────────────── */
const T = {
  bg:          '#03040a',
  nav:         '#07090f',
  surface:     '#0d1018',
  surfaceUp:   '#121620',
  border:      'rgba(255,255,255,0.055)',
  borderMid:   'rgba(255,255,255,0.11)',
  amber:       '#e6a72a',
  amberDim:    'rgba(230,167,42,0.1)',
  amberBorder: 'rgba(230,167,42,0.28)',
  green:       '#2dd4a0',
  red:         '#f16a6a',
  cyan:        '#5bbfed',
  blue:        '#60a5fa',
  purple:      '#c084fc',
  orange:      '#f97316',
  text:        '#cdd3df',
  muted:       '#5e6573',
  dim:         '#2e333e',
  mono:        "var(--font-mono, 'IBM Plex Mono', 'Courier New', monospace)",
  sans:        "var(--font-sans, 'Plus Jakarta Sans', sans-serif)",
}

const SMA_CFG = {
  sma20:  { color: T.blue,   label: 'SMA 20'  },
  sma50:  { color: T.amber,  label: 'SMA 50'  },
  sma100: { color: T.purple, label: 'SMA 100' },
  sma200: { color: T.orange, label: 'SMA 200' },
}

/* ─── Indicator helpers ─────────────────────────────────────── */
function sma(arr, p) {
  return arr.map((_, i) => {
    if (i < p - 1) return null
    const sl = arr.slice(i - p + 1, i + 1).filter(v => v != null)
    if (sl.length < p) return null
    return +(sl.reduce((a, b) => a + b, 0) / p).toFixed(2)
  })
}
function bb(arr, p = 20, m = 2) {
  const mid = sma(arr, p)
  return arr.map((_, i) => {
    if (mid[i] == null) return { u: null, l: null }
    const sl = arr.slice(i - p + 1, i + 1)
    const std = Math.sqrt(sl.reduce((s, v) => s + (v - mid[i]) ** 2, 0) / p)
    return { u: +(mid[i] + m * std).toFixed(2), l: +(mid[i] - m * std).toFixed(2) }
  })
}
function buildSeries(raw) {
  const closes = raw.close || []
  const s20 = sma(closes, 20), s100 = sma(closes, 100)
  const bbs = bb(closes, 20, 2)
  return (raw.date || []).map((d, i) => ({
    date: d, i,
    close:   closes[i],
    sma20:   s20[i],
    sma50:   raw.sma50?.[i],
    sma100:  s100[i],
    sma200:  raw.sma200?.[i],
    bbUpper: bbs[i]?.u,
    bbLower: bbs[i]?.l,
    rsi:     raw.rsi?.[i],
    macdLine: raw.macd?.[i],
    macdSig:  raw.macd_signal?.[i],
    macdHist: raw.macd_hist?.[i],
    volume:   raw.volume?.[i],
  }))
}

/* ─── Shared UI atoms ───────────────────────────────────────── */
const SIG_C = {
  buy:'#2dd4a0', sell:'#f16a6a', hold:'#e6a72a',
  high:'#f16a6a', low:'#2dd4a0', moderate:'#e6a72a', 'very high':'#f16a6a',
  BULLISH:'#2dd4a0', BEARISH:'#f16a6a', NEUTRAL:T.muted,
  ABOVE:'#5bbfed', EXCELLENT:'#2dd4a0', STRONG:'#2dd4a0', HEALTHY:'#5bbfed',
  FAIR:'#5bbfed', HIGH:'#f0954c', ELEVATED:'#f0954c',
}
const sc = v => SIG_C[v] || T.muted

const Badge = ({ v, label }) => {
  const c = sc(v || label)
  return (
    <span style={{ fontSize:10, fontFamily:T.mono, fontWeight:700, color:c,
      background:c+'1a', border:`1px solid ${c}44`, padding:'1px 6px',
      borderRadius:3, letterSpacing:'0.04em', whiteSpace:'nowrap' }}>
      {label || v}
    </span>
  )
}

const Panel = ({ title, children, style }) => (
  <div style={{ border:`1px solid ${T.border}`, borderRadius:8, overflow:'hidden', ...style }}>
    <div style={{ padding:'7px 13px', background:'rgba(255,255,255,0.022)',
      borderBottom:`1px solid ${T.border}` }}>
      <span style={{ fontSize:10, fontFamily:T.sans, fontWeight:700,
        color:T.amber, letterSpacing:'0.04em', textTransform:'uppercase' }}>{title}</span>
    </div>
    {children}
  </div>
)

const Row = ({ label, value, badge, note }) => (
  <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
    padding:'7px 13px', borderBottom:`1px solid ${T.border}` }}>
    <span style={{ fontSize:12, color:T.muted, fontFamily:T.mono }}>{label}</span>
    <div style={{ display:'flex', alignItems:'center', gap:8 }}>
      {note && <span style={{ fontSize:11, color:T.dim, fontFamily:T.mono }}>{note}</span>}
      <span style={{ fontSize:13, color:T.text, fontFamily:T.mono, fontWeight:700 }}>{value ?? '—'}</span>
      {badge && <Badge v={badge} />}
    </div>
  </div>
)

const KPI = ({ label, value }) => (
  <div style={{ background:T.surface, border:`1px solid ${T.border}`, borderRadius:6, padding:'10px 13px' }}>
    <div style={{ fontSize:10, color:T.muted, fontFamily:T.mono, marginBottom:5 }}>{label}</div>
    <div style={{ fontSize:15, color:T.text, fontFamily:T.mono, fontWeight:700 }}>{value ?? '—'}</div>
  </div>
)

const tip = { background:T.surfaceUp, border:`1px solid ${T.border}`,
  borderRadius:6, fontSize:11, fontFamily:T.mono, color:T.text }

const fmtPct  = v => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
const fmtMult = v => v == null ? '—' : `${v.toFixed(1)}%`
const fmtX    = v => v == null ? '—' : `${(+v).toFixed(1)}×`
const fmtCap  = v => {
  if (v == null) return '—'
  if (v >= 1e12) return `$${(v/1e12).toFixed(2)}T`
  if (v >= 1e9)  return `$${(v/1e9).toFixed(1)}B`
  return `$${v.toLocaleString()}`
}

/* ─── Price Chart with SMA overlays ────────────────────────── */
function PriceLineChart({ chartData, showSMA, showBB, height = 180 }) {
  const smaKeys = Object.entries(showSMA).filter(([,v]) => v).map(([k]) => k)
  const subset = chartData.filter(d => d.close != null)
  const closes = subset.map(d => d.close)
  const smaPts = smaKeys.flatMap(k => subset.map(d => d[k]).filter(v => v != null))
  const bbPts  = showBB ? subset.flatMap(d => [d.bbUpper, d.bbLower]).filter(v => v != null) : []
  const all    = [...closes, ...smaPts, ...bbPts]
  const lo = Math.min(...all) * 0.975
  const hi = Math.max(...all) * 1.025

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={subset} margin={{ top:4, right:2, bottom:0, left:2 }}>
        <defs>
          <linearGradient id="pcg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor={T.green} stopOpacity={0.18} />
            <stop offset="100%" stopColor={T.green} stopOpacity={0.01} />
          </linearGradient>
        </defs>
        <YAxis domain={[lo, hi]} hide />
        <XAxis dataKey="date" hide />
        <Tooltip contentStyle={tip}
          formatter={(v, name) => v == null ? ['—', name] : [`$${(+v).toFixed(2)}`, name === 'close' ? 'Price' : SMA_CFG[name]?.label || name]}
          labelFormatter={d => d} />
        {showBB && (
          <>
            <Area type="monotone" dataKey="bbUpper" fill="none" stroke={T.cyan} strokeWidth={1} strokeDasharray="4,3" opacity={0.45} dot={false} connectNulls />
            <Area type="monotone" dataKey="bbLower" fill="none" stroke={T.cyan} strokeWidth={1} strokeDasharray="4,3" opacity={0.45} dot={false} connectNulls />
          </>
        )}
        <Area type="monotone" dataKey="close" stroke={T.green} strokeWidth={1.6} fill="url(#pcg)" dot={false} />
        {smaKeys.map(k => (
          <Line key={k} type="monotone" dataKey={k} stroke={SMA_CFG[k].color} strokeWidth={1.6} dot={false} connectNulls />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/* ─── Box Plot ─────────────────────────────────────────────── */
function BoxPlot({ chartData, currentPrice }) {
  const ref = useRef(null)
  const [w, setW] = useState(680)
  useEffect(() => {
    const obs = new ResizeObserver(e => setW(e[0]?.contentRect.width || 680))
    if (ref.current) obs.observe(ref.current)
    return () => obs.disconnect()
  }, [])

  const closes = chartData.map(d => d.close).filter(v => v != null).sort((a, b) => a - b)
  if (closes.length < 4) return null
  const n  = closes.length
  const mn = closes[0], q1 = closes[Math.floor(n*.25)], md = closes[Math.floor(n*.5)]
  const q3 = closes[Math.floor(n*.75)], mx = closes[n-1]
  const cur = Math.min(Math.max(currentPrice, mn), mx)

  const H = 90, PAD = { l:60, r:60, t:28, b:30 }
  const cW = w - PAD.l - PAD.r
  const midY = PAD.t + (H - PAD.t - PAD.b) / 2
  const xOf = v => PAD.l + ((v - mn) / (mx - mn)) * cW

  return (
    <div ref={ref} style={{ width:'100%', padding:'8px 0 4px' }}>
      <svg width={w} height={H} style={{ display:'block' }}>
        <line x1={xOf(mn)} x2={xOf(mx)} y1={midY} y2={midY} stroke={T.borderMid} strokeWidth={1.5} />
        {[mn,mx].map((v,i) => <line key={i} x1={xOf(v)} x2={xOf(v)} y1={midY-8} y2={midY+8} stroke={T.muted} strokeWidth={1.5} />)}
        <rect x={xOf(q1)} y={midY-10} width={xOf(q3)-xOf(q1)} height={20} fill={T.amberDim} stroke={T.amberBorder} strokeWidth={1} rx={2} />
        <line x1={xOf(md)} x2={xOf(md)} y1={midY-11} y2={midY+11} stroke={T.amber} strokeWidth={2} />
        <circle cx={xOf(cur)} cy={midY} r={5} fill={T.green} opacity={0.9} />
        <line x1={xOf(cur)} x2={xOf(cur)} y1={midY-16} y2={midY-6} stroke={T.green} strokeWidth={1.5} />
        {[[mn,'Min'],[q1,'Q1'],[md,'Med'],[q3,'Q3'],[mx,'Max']].map(([v,l]) => (
          <g key={l}>
            <text x={xOf(v)} y={H-4} textAnchor="middle" fontSize={10} fill={T.muted} fontFamily={T.mono}>{l}</text>
            <text x={xOf(v)} y={PAD.t-6} textAnchor="middle" fontSize={10} fill={T.text} fontFamily={T.mono} fontWeight="bold">
              ${v>=1000?(v/1000).toFixed(1)+'k':v.toFixed(0)}
            </text>
          </g>
        ))}
        <text x={xOf(cur)} y={midY-20} textAnchor="middle" fontSize={10} fill={T.green} fontFamily={T.mono} fontWeight="bold">
          ▲ ${cur.toFixed(0)}
        </text>
      </svg>
      <div style={{ display:'flex', gap:16, padding:'4px 12px', flexWrap:'wrap' }}>
        {[
          { c:T.amber,  t:`IQR Box (Q1–Q3)`              },
          { c:T.amber,  t:`Median  $${md.toFixed(0)}`, fw:700 },
          { c:T.green,  t:`▲ Current  $${currentPrice.toFixed(2)}` },
          { c:T.muted,  t:`Range  $${mn.toFixed(0)} – $${mx.toFixed(0)}` },
        ].map(({ c, t, fw }, i) => (
          <span key={i} style={{ fontSize:11, color:c, fontFamily:T.mono, fontWeight:fw||400 }}>{t}</span>
        ))}
      </div>
    </div>
  )
}

/* ─── TAB: OVERVIEW ─────────────────────────────────────────── */
const RANGE = { '1M':22, '3M':63, '6M':126, '1Y':9999 }

function OverviewTab({ data, chartData }) {
  const [rng, setRng]   = useState('3M')
  const [sma, setSma]   = useState({ sma20:true, sma50:true, sma100:false })
  const [showBB, setBB] = useState(false)

  const { meta, quote, fundamentals: f, analyst: a } = data
  const slice = chartData.slice(-RANGE[rng])

  const pos = (quote.day_change_pct || 0) > 0
  const total = (a.num_analysts || 0)
  const target = a.target_mean

  const recLabel = {
    strong_buy:'STRONG BUY', buy:'BUY', hold:'HOLD', sell:'SELL', strong_sell:'STRONG SELL'
  }[a.recommendation] || (a.recommendation || '').toUpperCase().replace('_',' ')
  const recColor = ['strong_buy','buy'].includes(a.recommendation) ? T.green :
                   ['sell','strong_sell'].includes(a.recommendation) ? T.red : T.amber

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 240px', gap:14 }}>
        {/* Price chart */}
        <Panel title={`Price chart — ${rng}`}>
          <div style={{ padding:'10px 13px 6px' }}>
            <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginBottom:10 }}>
              {Object.keys(RANGE).map(r => (
                <button key={r} onClick={() => setRng(r)} style={{
                  fontSize:11, fontFamily:T.mono, cursor:'pointer', padding:'2px 9px', borderRadius:3,
                  border:`1px solid ${r===rng?T.amber:'transparent'}`,
                  background:r===rng?T.amberDim:'transparent', color:r===rng?T.amber:T.muted,
                }}>{r}</button>
              ))}
              <div style={{ width:1, background:T.border }} />
              {Object.entries(SMA_CFG).slice(0,3).map(([k,m]) => (
                <button key={k} onClick={() => setSma(p => ({...p,[k]:!p[k]}))} style={{
                  fontSize:11, fontFamily:T.mono, cursor:'pointer', padding:'2px 8px', borderRadius:3,
                  border:`1px solid ${sma[k]?m.color+'80':'transparent'}`,
                  background:sma[k]?m.color+'18':'transparent', color:sma[k]?m.color:T.dim,
                }}>{m.label}</button>
              ))}
              <button onClick={() => setBB(p => !p)} style={{
                fontSize:11, fontFamily:T.mono, cursor:'pointer', padding:'2px 8px', borderRadius:3,
                border:`1px solid ${showBB?T.cyan+'80':'transparent'}`,
                background:showBB?T.cyan+'18':'transparent', color:showBB?T.cyan:T.dim,
              }}>BB</button>
            </div>
            <PriceLineChart chartData={slice} showSMA={sma} showBB={showBB} />
          </div>
        </Panel>

        {/* Analyst consensus */}
        <Panel title="Wall St. Consensus">
          <div style={{ padding:13 }}>
            <div style={{ textAlign:'center', marginBottom:12 }}>
              <div style={{ fontFamily:T.sans, fontWeight:700, fontSize:18, color:recColor, letterSpacing:'0.01em' }}>
                {recLabel || '—'}
              </div>
              <div style={{ fontSize:11, color:T.muted, fontFamily:T.mono, marginTop:2 }}>
                {total} analysts covering
              </div>
            </div>
            <div style={{ borderTop:`1px solid ${T.border}`, paddingTop:10 }}>
              <div style={{ fontSize:10, color:T.muted, fontFamily:T.mono, marginBottom:4 }}>12-MONTH PRICE TARGET</div>
              {target ? (
                <>
                  <div style={{ fontSize:22, fontFamily:T.mono, fontWeight:700, color:T.amber }}>${target.toLocaleString()}</div>
                  <div style={{ fontSize:11, color:T.muted, fontFamily:T.mono, marginTop:2 }}>
                    Low ${a.target_low?.toLocaleString() || '—'}  ·  High ${a.target_high?.toLocaleString() || '—'}
                  </div>
                  {a.target_upside_pct != null && (
                    <div style={{ fontSize:12, fontFamily:T.mono, marginTop:6,
                      color: a.target_upside_pct > 0 ? T.green : T.red, fontWeight:700 }}>
                      {a.target_upside_pct > 0 ? '+' : ''}{a.target_upside_pct.toFixed(1)}% implied upside
                    </div>
                  )}
                </>
              ) : <div style={{ fontSize:14, color:T.muted, fontFamily:T.mono }}>—</div>}
            </div>
          </div>
        </Panel>
      </div>

      {/* KPI Grid */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:8 }}>
        {[
          ['MARKET CAP',    fmtCap(f.market_cap)],
          ['P/E (TTM)',      fmtX(f.trailing_pe)],
          ['FORWARD P/E',   fmtX(f.forward_pe)],
          ['52W HIGH',      quote.fifty_two_week_high != null ? `$${quote.fifty_two_week_high.toLocaleString()}` : '—'],
          ['52W LOW',       quote.fifty_two_week_low  != null ? `$${quote.fifty_two_week_low.toLocaleString()}`  : '—'],
          ['BETA',          f.beta?.toFixed(2) ?? '—'],
          ['DIV YIELD',     f.dividend_yield != null ? fmtMult(f.dividend_yield * 100) : '—'],
          ['PEG RATIO',     f.peg_ratio?.toFixed(2) ?? '—'],
        ].map(([l, v]) => <KPI key={l} label={l} value={v} />)}
      </div>

      {/* About */}
      <Panel title="Company Overview">
        <div style={{ padding:13 }}>
          <div style={{ display:'flex', gap:22, flexWrap:'wrap' }}>
            {[
              ['TICKER',   meta.ticker],
              ['EXCHANGE', meta.exchange],
              ['SECTOR',   meta.sector],
              ['INDUSTRY', meta.industry],
              ['CURRENCY', meta.currency],
            ].filter(([,v]) => v).map(([l,v]) => (
              <div key={l}>
                <div style={{ fontSize:10, color:T.dim, fontFamily:T.mono }}>{l}</div>
                <div style={{ fontSize:12, color:T.text, fontFamily:T.mono, marginTop:2 }}>{v}</div>
              </div>
            ))}
          </div>
          <div style={{ marginTop:10, fontSize:11, color:T.muted, fontFamily:T.mono }}>
            Data source: {data.data_source} · {data.as_of}
          </div>
        </div>
      </Panel>
    </div>
  )
}

/* ─── TAB: FUNDAMENTALS ─────────────────────────────────────── */
function FundamentalsTab({ data }) {
  const f = data.fundamentals
  const fmt = (v, pct) => {
    if (v == null) return '—'
    return pct ? `${(v*100).toFixed(1)}%` : (+v).toFixed(2)
  }

  const rows = [
    /* Valuation */
    { section:'VALUATION',        label:'P/E Ratio (TTM)',    value: fmtX(f.trailing_pe),                badge: f.trailing_pe > 50 ? 'HIGH' : f.trailing_pe > 25 ? 'ELEVATED' : 'FAIR' },
    { label:'Forward P/E',        value: fmtX(f.forward_pe),  badge: f.forward_pe > 35 ? 'ELEVATED' : 'FAIR' },
    { label:'PEG Ratio',          value: f.peg_ratio?.toFixed(2) ?? '—', badge: f.peg_ratio < 1 ? 'EXCELLENT' : f.peg_ratio < 2 ? 'FAIR' : 'HIGH' },
    /* Profitability */
    { section:'PROFITABILITY',    label:'Net Profit Margin',  value: fmt(f.profit_margin, true), badge: (f.profit_margin||0) > 0.2 ? 'EXCELLENT' : (f.profit_margin||0) > 0 ? 'FAIR' : 'BEARISH' },
    /* Growth */
    { section:'GROWTH',           label:'Revenue Growth YoY', value: fmt(f.revenue_growth, true), badge: (f.revenue_growth||0) > 0.15 ? 'STRONG' : (f.revenue_growth||0) > 0 ? 'FAIR' : 'BEARISH' },
    { label:'Earnings Growth YoY',value: fmt(f.earnings_growth, true), badge: (f.earnings_growth||0) > 0.15 ? 'STRONG' : (f.earnings_growth||0) > 0 ? 'FAIR' : 'BEARISH' },
    /* Risk */
    { section:'RISK',             label:'Beta',               value: f.beta?.toFixed(2) ?? '—', badge: (f.beta||0) > 1.5 ? 'HIGH' : (f.beta||0) > 1 ? 'ELEVATED' : 'FAIR' },
    { label:'Dividend Yield',     value: f.dividend_yield != null ? `${(f.dividend_yield*100).toFixed(2)}%` : '—' },
  ]

  let lastSection = null
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      <Panel title="Financial Metrics">
        {rows.map((r, i) => {
          const showHeader = r.section && r.section !== lastSection
          if (r.section) lastSection = r.section
          return (
            <div key={i}>
              {showHeader && (
                <div style={{ padding:'6px 13px 4px', fontSize:10, color:T.amber,
                  fontFamily:T.sans, fontWeight:700, letterSpacing:'0.08em',
                  background:'rgba(230,167,42,0.05)', borderBottom:`1px solid ${T.border}` }}>
                  {r.section}
                </div>
              )}
              <Row label={r.label} value={r.value} badge={r.badge} />
            </div>
          )
        })}
      </Panel>
      <div style={{ padding:'8px 13px', fontSize:11, color:T.dim, fontFamily:T.mono,
        background:T.surface, border:`1px solid ${T.border}`, borderRadius:8 }}>
        Data from Yahoo Finance. Additional balance-sheet fields (current ratio, D/E, cash)
        available with a backend enhancement to <code>market_data.py</code>.
      </div>
    </div>
  )
}

/* ─── TAB: TECHNICALS ───────────────────────────────────────── */
function TechnicalsTab({ data }) {
  const i = data.indicators
  const isGolden = (i.trend || '').includes('golden')

  const cards = [
    { label:'Trend (50d vs 200d)', value: isGolden ? '✓ Golden Cross' : '✗ Death Cross',
      color: isGolden ? T.green : T.red,
      note: `50d: $${(i.sma50||0).toFixed(0)}  ·  200d: $${(i.sma200||0).toFixed(0)}` },
    { label:'vs SMA 50',  value: fmtPct(i.price_vs_sma50_pct),
      color: (i.price_vs_sma50_pct||0) > 0 ? T.green : T.red },
    { label:'vs SMA 200', value: fmtPct(i.price_vs_sma200_pct),
      color: (i.price_vs_sma200_pct||0) > 0 ? T.green : T.red },
    { label:'RSI (14)',   value: i.rsi?.toFixed(1) ?? '—',
      color: (i.rsi||0) > 70 ? T.red : (i.rsi||0) < 30 ? T.green : T.text,
      note: i.rsi_zone || '' },
    { label:'MACD',       value: i.macd?.toFixed(2) ?? '—',
      color: (i.macd_state||'').includes('bullish') ? T.green : T.red,
      note: i.macd_state ? (i.macd_state.includes('bullish') ? '↑ Bullish' : '↓ Bearish') : '' },
    { label:'MACD Signal',value: i.macd_signal?.toFixed(2) ?? '—', color:T.text },
  ]

  const levels = [
    { type:'RESISTANCE', level: data.quote.fifty_two_week_high ? `$${data.quote.fifty_two_week_high.toFixed(2)}` : '—', strength:'52W HIGH' },
    { type:'CURRENT',    level: data.quote.price ? `$${data.quote.price.toFixed(2)}` : '—' },
    { type:'SUPPORT',    level: data.quote.fifty_two_week_low  ? `$${data.quote.fifty_two_week_low.toFixed(2)}`  : '—', strength:'52W LOW' },
  ]

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:10 }}>
        {cards.map(({ label, value, color, note }) => (
          <div key={label} style={{ background:T.surface, border:`1px solid ${T.border}`, borderRadius:8, padding:'12px 14px' }}>
            <div style={{ fontSize:10, color:T.muted, fontFamily:T.mono, marginBottom:5 }}>{label}</div>
            <div style={{ fontSize:17, color, fontFamily:T.mono, fontWeight:700 }}>{value}</div>
            {note && <div style={{ fontSize:11, color:T.dim, fontFamily:T.mono, marginTop:4 }}>{note}</div>}
          </div>
        ))}
      </div>

      <Panel title="Key Price Levels">
        {levels.map((lvl, i) => {
          const c = lvl.type === 'RESISTANCE' ? T.red : lvl.type === 'CURRENT' ? T.amber : T.green
          return (
            <div key={i} style={{ display:'flex', alignItems:'center', padding:'8px 13px',
              borderBottom:`1px solid ${T.border}`,
              background:lvl.type==='CURRENT'?'rgba(230,167,42,0.06)':'transparent' }}>
              <span style={{ fontSize:10, fontFamily:T.mono, fontWeight:700, color:c, minWidth:96 }}>{lvl.type}</span>
              <div style={{ flex:1, height:1, background:lvl.type==='CURRENT'?T.amber+'55':T.border, margin:'0 14px' }} />
              <span style={{ fontSize:14, fontFamily:T.mono, fontWeight:700, color:T.text, marginRight:10 }}>{lvl.level}</span>
              {lvl.strength && <Badge v={lvl.strength} label={lvl.strength} />}
            </div>
          )
        })}
      </Panel>
    </div>
  )
}

/* ─── TAB: CHARTS ───────────────────────────────────────────── */
function ChartsTab({ data, chartData }) {
  const [showSMA, setSMA] = useState({ sma20:true, sma50:true, sma100:true, sma200:false })
  const [showBB, setBB]   = useState(true)
  const [days, setDays]   = useState(90)

  const candles = chartData.slice(-days)
  const rsiData = chartData.filter(d => d.rsi != null).slice(-60)
  const macdData = chartData.filter(d => d.macdLine != null).slice(-60)
  const volData  = candles.filter(d => d.volume != null)

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      {/* Controls */}
      <div style={{ display:'flex', gap:6, flexWrap:'wrap', alignItems:'center',
        padding:'8px 13px', background:T.surface, border:`1px solid ${T.border}`, borderRadius:8 }}>
        <span style={{ fontSize:11, color:T.muted, fontFamily:T.mono, marginRight:4 }}>OVERLAYS:</span>
        {Object.entries(SMA_CFG).map(([k,m]) => (
          <button key={k} onClick={() => setSMA(p => ({...p,[k]:!p[k]}))} style={{
            fontSize:11, fontFamily:T.mono, cursor:'pointer', padding:'3px 10px', borderRadius:4,
            border:`1px solid ${showSMA[k]?m.color+'80':T.border}`,
            background:showSMA[k]?m.color+'18':'transparent', color:showSMA[k]?m.color:T.dim,
          }}>{m.label}</button>
        ))}
        <button onClick={() => setBB(p => !p)} style={{
          fontSize:11, fontFamily:T.mono, cursor:'pointer', padding:'3px 10px', borderRadius:4,
          border:`1px solid ${showBB?T.cyan+'80':T.border}`,
          background:showBB?T.cyan+'18':'transparent', color:showBB?T.cyan:T.dim,
        }}>Bollinger Bands (20,2)</button>
        <div style={{ marginLeft:'auto', display:'flex', gap:5 }}>
          {[30,60,90,180].map(n => (
            <button key={n} onClick={() => setDays(n)} style={{
              fontSize:11, fontFamily:T.mono, cursor:'pointer', padding:'3px 8px', borderRadius:3,
              border:`1px solid ${days===n?T.amber:'transparent'}`,
              background:days===n?T.amberDim:'transparent', color:days===n?T.amber:T.muted,
            }}>{n}D</button>
          ))}
        </div>
      </div>

      {/* SMA legend */}
      <div style={{ display:'flex', gap:14, flexWrap:'wrap', padding:'0 4px' }}>
        {Object.entries(SMA_CFG).filter(([k]) => showSMA[k]).map(([k,m]) => (
          <div key={k} style={{ display:'flex', alignItems:'center', gap:5 }}>
            <div style={{ width:20, height:2.5, background:m.color, borderRadius:2 }} />
            <span style={{ fontSize:11, color:m.color, fontFamily:T.mono }}>{m.label}</span>
          </div>
        ))}
        {showBB && (
          <div style={{ display:'flex', alignItems:'center', gap:5 }}>
            <div style={{ width:20, height:1.5, background:T.cyan, borderRadius:2, opacity:0.5 }} />
            <span style={{ fontSize:11, color:T.cyan, fontFamily:T.mono, opacity:0.7 }}>Bollinger Bands</span>
          </div>
        )}
      </div>

      {/* Main price chart */}
      <Panel title={`Price — SMA & Bollinger Bands · ${days}-day`}>
        <div style={{ padding:'10px 4px 6px' }}>
          <PriceLineChart chartData={candles} showSMA={showSMA} showBB={showBB} height={200} />
        </div>
      </Panel>

      {/* Volume */}
      <Panel title="Volume">
        <div style={{ padding:'8px 4px 4px' }}>
          <ResponsiveContainer width="100%" height={72}>
            <BarChart data={volData} margin={{ top:2, right:4, bottom:0, left:4 }}>
              <YAxis hide />
              <XAxis dataKey="date" hide />
              <Tooltip contentStyle={tip} formatter={v => [`${(+v/1e6).toFixed(1)}M`, 'Volume']} labelFormatter={d => d} />
              <Bar dataKey="volume" radius={[1,1,0,0]}>
                {volData.map((d, i) => (
                  <Cell key={i} fill={i > 0 && d.close >= volData[i-1]?.close ? T.green : T.red} opacity={0.65} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      {/* RSI + MACD side by side */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:14 }}>
        <Panel title="RSI (14) — Wilder's Smoothing">
          <div style={{ padding:'8px 4px 4px' }}>
            <ResponsiveContainer width="100%" height={110}>
              <LineChart data={rsiData} margin={{ top:4, right:4, bottom:0, left:4 }}>
                <YAxis domain={[0,100]} hide />
                <XAxis dataKey="date" hide />
                <ReferenceLine y={70} stroke={T.red}   strokeDasharray="3,3" strokeWidth={0.8} opacity={0.6} />
                <ReferenceLine y={50} stroke={T.dim}   strokeDasharray="3,3" strokeWidth={0.6} opacity={0.5} />
                <ReferenceLine y={30} stroke={T.green} strokeDasharray="3,3" strokeWidth={0.8} opacity={0.6} />
                <Tooltip contentStyle={tip} formatter={v => [v == null ? '—' : (+v).toFixed(1), 'RSI']} labelFormatter={d => d} />
                <Line type="monotone" dataKey="rsi" stroke={T.blue} strokeWidth={1.6} dot={false} connectNulls />
              </LineChart>
            </ResponsiveContainer>
            <div style={{ display:'flex', justifyContent:'space-between', padding:'0 12px 6px', fontSize:10, fontFamily:T.mono }}>
              <span style={{ color:T.green }}>30 oversold</span>
              <span style={{ color:T.muted }}>50 neutral</span>
              <span style={{ color:T.red }}>70 overbought</span>
            </div>
          </div>
        </Panel>

        <Panel title="MACD (12, 26, 9)">
          <div style={{ padding:'8px 4px 4px' }}>
            <ResponsiveContainer width="100%" height={110}>
              <ComposedChart data={macdData} margin={{ top:4, right:4, bottom:0, left:4 }}>
                <YAxis hide />
                <XAxis dataKey="date" hide />
                <ReferenceLine y={0} stroke={T.borderMid} strokeWidth={1} />
                <Tooltip contentStyle={tip}
                  formatter={(v, name) => [v == null ? '—' : (+v).toFixed(2), name === 'macdHist' ? 'Histogram' : name === 'macdLine' ? 'MACD' : 'Signal']}
                  labelFormatter={d => d} />
                <Bar dataKey="macdHist" radius={[1,1,0,0]}>
                  {macdData.map((d, i) => (
                    <Cell key={i} fill={(d.macdHist||0) >= 0 ? T.green : T.red} opacity={0.55} />
                  ))}
                </Bar>
                <Line type="monotone" dataKey="macdLine" stroke={T.blue}   strokeWidth={1.6} dot={false} connectNulls name="macdLine" />
                <Line type="monotone" dataKey="macdSig"  stroke={T.orange} strokeWidth={1.4} dot={false} connectNulls name="macdSig" strokeDasharray="4,2" />
              </ComposedChart>
            </ResponsiveContainer>
            <div style={{ display:'flex', gap:12, padding:'4px 12px 6px', fontSize:11, fontFamily:T.mono }}>
              <span style={{ color:T.blue }}>— MACD</span>
              <span style={{ color:T.orange }}>- - Signal</span>
              <span style={{ color:T.green }}>█ +Hist</span>
              <span style={{ color:T.red }}>█ −Hist</span>
            </div>
          </div>
        </Panel>
      </div>

      {/* Box Plot */}
      <Panel title={`52-Week Price Distribution — Box & Whisker (${chartData.length} sessions)`}>
        <BoxPlot chartData={chartData} currentPrice={data.quote.price || 0} />
      </Panel>
    </div>
  )
}

/* ─── TAB: ASSESSMENT ───────────────────────────────────────── */
function AssessmentTab({ data }) {
  const a = data.assessment
  if (!a) {
    return (
      <Panel title="AI / Rules Assessment">
        <div style={{ padding:20, color:T.muted, fontFamily:T.mono, fontSize:13 }}>
          {data.ai_error || 'No assessment available. Check backend configuration.'}
        </div>
      </Panel>
    )
  }

  const sigColor = { buy:T.green, sell:T.red, hold:T.amber }
  const riskColor = { low:T.green, moderate:T.amber, high:T.red, 'very high':T.red }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      {/* Signal header */}
      <div style={{ border:`1px solid ${T.border}`, borderRadius:8, padding:'16px 18px',
        background: sigColor[a.signal] + '08' }}>
        <div style={{ display:'flex', flexWrap:'wrap', gap:14, alignItems:'center', marginBottom:14 }}>
          <div style={{ fontFamily:T.sans, fontWeight:700, fontSize:26, color:sigColor[a.signal], letterSpacing:'0.01em' }}>
            {(a.signal || '').toUpperCase()}
          </div>
          <div>
            <div style={{ fontSize:11, color:T.muted, fontFamily:T.mono }}>CONVICTION</div>
            <div style={{ fontSize:14, color:T.text, fontFamily:T.mono, fontWeight:700, textTransform:'uppercase' }}>{a.conviction}</div>
          </div>
          <div>
            <div style={{ fontSize:11, color:T.muted, fontFamily:T.mono }}>RISK LEVEL</div>
            <div style={{ fontSize:14, fontFamily:T.mono, fontWeight:700, textTransform:'uppercase', color:riskColor[a.risk_level] || T.text }}>{a.risk_level}</div>
          </div>
          <div>
            <div style={{ fontSize:11, color:T.muted, fontFamily:T.mono }}>HORIZON</div>
            <div style={{ fontSize:13, color:T.text, fontFamily:T.mono }}>{a.time_horizon}</div>
          </div>
          <div style={{ marginLeft:'auto' }}>
            <Badge v={a.engine?.toUpperCase()} label={`Engine: ${a.engine || '—'}`} />
          </div>
        </div>
        <p style={{ fontSize:13, color:T.text, lineHeight:1.7, margin:0 }}>{a.summary}</p>
      </div>

      {/* Bull/Bear factors */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:14 }}>
        <Panel title="Bullish Factors">
          <div style={{ padding:'8px 0' }}>
            {(a.bullish_factors || []).map((f, i) => (
              <div key={i} style={{ display:'flex', gap:10, padding:'6px 13px', borderBottom:`1px solid ${T.border}` }}>
                <span style={{ color:T.green, fontSize:14, flexShrink:0 }}>↑</span>
                <span style={{ fontSize:12, color:T.text, lineHeight:1.65 }}>{f}</span>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Bearish Factors">
          <div style={{ padding:'8px 0' }}>
            {(a.bearish_factors || []).map((f, i) => (
              <div key={i} style={{ display:'flex', gap:10, padding:'6px 13px', borderBottom:`1px solid ${T.border}` }}>
                <span style={{ color:T.red, fontSize:14, flexShrink:0 }}>↓</span>
                <span style={{ fontSize:12, color:T.text, lineHeight:1.65 }}>{f}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {/* Technical read + Reasoning */}
      {[['Technical Read', a.technical_read], ['Reasoning', a.reasoning]].map(([title, text]) => (
        <Panel key={title} title={title}>
          <div style={{ padding:13 }}>
            <p style={{ fontSize:13, color:T.muted, lineHeight:1.75, margin:0 }}>{text}</p>
          </div>
        </Panel>
      ))}

      {/* Disclaimer */}
      <div style={{ padding:'10px 14px', background:T.amberDim, border:`1px solid ${T.amberBorder}`,
        borderRadius:8, fontSize:11, color:T.amber, fontFamily:T.mono, lineHeight:1.65 }}>
        ⚠️ {a.disclaimer || data.disclaimer}
      </div>
    </div>
  )
}

/* ─── MAIN APP ──────────────────────────────────────────────── */
const TABS = [
  { id:'overview',    label:'OVERVIEW'    },
  { id:'fundamentals',label:'FUNDAMENTALS'},
  { id:'technicals',  label:'TECHNICALS'  },
  { id:'charts',      label:'CHARTS'      },
  { id:'assessment',  label:'⚡ ASSESSMENT'},
]

export default function App() {
  const [search, setSearch]   = useState('')
  const [data,   setData]     = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)
  const [tab,     setTab]     = useState('overview')
  const [chartData, setChartData] = useState([])

  async function run(sym) {
    const s = (sym || search).trim().toUpperCase()
    if (!s) return
    setLoading(true); setError(null)
    try {
      const result = await analyzeTicker(s)
      setData(result)
      setChartData(buildSeries(result.series || {}))
      setTab('overview')
    } catch (e) {
      setError(e.message)
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  const pos = data ? (data.quote?.day_change_pct || 0) > 0 : false

  return (
    <div style={{ background:T.bg, minHeight:'100vh', color:T.text }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');
        *{box-sizing:border-box;margin:0;padding:0;}
        body{background:${T.bg}; font-family:var(--font-sans,'Plus Jakarta Sans',sans-serif);}
        ::-webkit-scrollbar{width:4px;height:4px;}
        ::-webkit-scrollbar-track{background:${T.surface};}
        ::-webkit-scrollbar-thumb{background:#2d3748;border-radius:3px;}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
        input::placeholder{color:${T.dim};}
        button{font-family:inherit;}
      `}</style>

      {/* ── NAV ── */}
      <div style={{ background:T.nav, borderBottom:`1px solid ${T.border}`, padding:'0 24px',
        height:50, display:'flex', alignItems:'center', gap:20, position:'sticky', top:0, zIndex:100 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8, flexShrink:0 }}>
          <span style={{ fontFamily:T.sans, fontWeight:700, fontSize:20, color:T.amber }}>ANALYST</span>
          <span style={{ fontFamily:T.sans, fontSize:20, color:T.muted }}>AGENT</span>
        </div>
        <div style={{ display:'flex', gap:6, flex:1, maxWidth:400 }}>
          <input value={search} onChange={e => setSearch(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && run()}
            placeholder="Ticker symbol (AAPL, NVDA, TSLA...)"
            style={{ flex:1, background:'#0f121a', border:`1px solid ${T.border}`, borderRadius:6,
              padding:'6px 12px', color:T.text, fontSize:12, fontFamily:T.mono, outline:'none' }} />
          <button onClick={() => run()} disabled={loading} style={{
            background:loading?T.amberDim:T.amberDim, border:`1px solid ${T.amberBorder}`,
            borderRadius:6, padding:'6px 14px', color:T.amber, fontSize:11,
            fontFamily:T.mono, fontWeight:700, cursor:loading?'wait':'pointer' }}>
            {loading ? 'ANALYZING…' : 'ANALYZE'}
          </button>
        </div>
        <div style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:16 }}>
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <span style={{ width:7, height:7, borderRadius:'50%', background:T.green,
              display:'inline-block', animation:'pulse 2s infinite' }} />
            <span style={{ fontSize:11, fontFamily:T.mono, color:T.green }}>MARKET OPEN</span>
          </div>
        </div>
      </div>

      {/* ── HERO ── */}
      {data && (
        <div style={{ background:T.nav, borderBottom:`1px solid ${T.border}`, padding:'13px 24px',
          display:'flex', alignItems:'center', gap:28, flexWrap:'wrap' }}>
          <div>
            <div style={{ display:'flex', alignItems:'baseline', gap:10, marginBottom:3 }}>
              <span style={{ fontFamily:T.sans, fontWeight:700, fontSize:26, color:T.amber }}>{data.meta.ticker}</span>
              <span style={{ fontSize:13, color:T.muted }}>{data.meta.name}</span>
            </div>
            <div style={{ fontSize:11, color:T.dim, fontFamily:T.mono }}>
              {[data.meta.exchange, data.meta.sector, data.meta.currency].filter(Boolean).join(' · ')}
            </div>
          </div>
          <div style={{ borderLeft:`1px solid ${T.border}`, paddingLeft:28 }}>
            <div style={{ display:'flex', alignItems:'baseline', gap:12 }}>
              <span style={{ fontFamily:T.mono, fontWeight:700, fontSize:28, color:T.text }}>
                ${(data.quote.price || 0).toLocaleString(undefined, { minimumFractionDigits:2, maximumFractionDigits:2 })}
              </span>
              <div>
                <div style={{ fontFamily:T.mono, fontWeight:700, fontSize:14, color:pos?T.green:T.red }}>
                  {fmtPct(data.quote.day_change_pct)}
                </div>
              </div>
            </div>
          </div>
          <div style={{ display:'flex', gap:24, borderLeft:`1px solid ${T.border}`, paddingLeft:28 }}>
            {[
              ['MKT CAP', fmtCap(data.fundamentals.market_cap)],
              ['P/E (TTM)', fmtX(data.fundamentals.trailing_pe)],
              ['BETA', data.fundamentals.beta?.toFixed(2) ?? '—'],
            ].map(([l,v]) => (
              <div key={l}>
                <div style={{ fontSize:10, color:T.dim, fontFamily:T.mono }}>{l}</div>
                <div style={{ fontSize:13, color:T.text, fontFamily:T.mono, fontWeight:700, marginTop:2 }}>{v}</div>
              </div>
            ))}
          </div>
          {data.assessment && (
            <div style={{ marginLeft:'auto', textAlign:'right' }}>
              <div style={{ fontSize:10, color:T.muted, fontFamily:T.mono, marginBottom:3 }}>AI ASSESSMENT</div>
              <div style={{ fontFamily:T.sans, fontWeight:700, fontSize:18, letterSpacing:'0.01em',
                color: {buy:T.green,sell:T.red,hold:T.amber}[data.assessment.signal] || T.text }}>
                {(data.assessment.signal || '').toUpperCase()}
              </div>
              <div style={{ fontSize:11, color:T.muted, fontFamily:T.mono }}>
                {data.assessment.conviction} conviction · {data.assessment.risk_level} risk
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── TABS ── */}
      {data && (
        <div style={{ background:T.nav, borderBottom:`1px solid ${T.border}`, padding:'0 24px', display:'flex' }}>
          {TABS.map(({ id, label }) => (
            <button key={id} onClick={() => setTab(id)} style={{
              padding:'11px 16px', fontSize:12, fontFamily:T.sans, fontWeight:700,
              letterSpacing:'0.01em', cursor:'pointer', border:'none', outline:'none',
              background:'transparent', color:tab===id?T.amber:T.muted,
              borderBottom:tab===id?`2px solid ${T.amber}`:'2px solid transparent',
              transition:'color 0.15s',
            }}>{label}</button>
          ))}
        </div>
      )}

      {/* ── CONTENT ── */}
      <div style={{ padding:'20px 24px 40px', maxWidth:1120 }}>
        {/* Landing */}
        {!data && !loading && !error && (
          <div style={{ paddingTop:80, textAlign:'center' }}>
            <div style={{ fontFamily:T.sans, fontSize:28, fontWeight:700, color:T.text, marginBottom:12 }}>
              Educational Stock Research
            </div>
            <div style={{ fontSize:14, color:T.muted, marginBottom:32 }}>
              Enter any ticker symbol to get technical indicators, fundamentals, and an AI assessment.
            </div>
            <div style={{ display:'flex', gap:8, justifyContent:'center', flexWrap:'wrap' }}>
              {['NVDA','AAPL','TSLA','AMZN','AVGO','RKLB'].map(t => (
                <button key={t} onClick={() => { setSearch(t); run(t) }} style={{
                  background:T.surface, border:`1px solid ${T.border}`, borderRadius:8,
                  padding:'8px 16px', color:T.amber, fontFamily:T.mono, fontSize:13,
                  fontWeight:700, cursor:'pointer' }}>{t}</button>
              ))}
            </div>
            <div style={{ marginTop:24, padding:'10px 16px', background:T.amberDim,
              border:`1px solid ${T.amberBorder}`, borderRadius:8, display:'inline-block',
              fontSize:12, color:T.amber, fontFamily:T.mono }}>
              Educational &amp; research use only · Not financial advice
            </div>
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div style={{ paddingTop:80, textAlign:'center', color:T.muted, fontFamily:T.mono, fontSize:14 }}>
            Fetching market data for {search}…
          </div>
        )}

        {/* Error */}
        {error && !loading && (
          <div style={{ marginTop:20, padding:'12px 16px', background:T.red+'12',
            border:`1px solid ${T.red}44`, borderRadius:8, color:T.red, fontFamily:T.mono, fontSize:13 }}>
            {error}
          </div>
        )}

        {/* Dashboard */}
        {data && !loading && (
          <>
            {tab === 'overview'     && <OverviewTab     data={data} chartData={chartData} />}
            {tab === 'fundamentals' && <FundamentalsTab data={data} />}
            {tab === 'technicals'   && <TechnicalsTab   data={data} />}
            {tab === 'charts'       && <ChartsTab        data={data} chartData={chartData} />}
            {tab === 'assessment'   && <AssessmentTab    data={data} />}
          </>
        )}
      </div>

      {/* ── STATUS BAR ── */}
      <div style={{ background:'#040408', borderTop:`1px solid ${T.amberBorder}`,
        padding:'5px 24px', display:'flex', alignItems:'center',
        position:'fixed', bottom:0, left:0, right:0 }}>
        {[
          ['⚡ ANALYST AGENT', T.amber],
          ['Powered by Groq llama-3.3-70b', T.muted],
          ['Educational purposes only · Not financial advice', T.dim],
        ].map(([text, color], i) => (
          <span key={i} style={{ fontSize:10, fontFamily:T.mono, color }}>
            {i > 0 && <span style={{ color:T.dim, margin:'0 10px' }}>·</span>}
            {text}
          </span>
        ))}
      </div>
    </div>
  )
}
