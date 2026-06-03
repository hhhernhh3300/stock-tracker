import { useState, useRef, useEffect } from "react";
import {
  AreaChart, Area, ComposedChart, Bar, Line,
  XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceLine, Cell,
} from "recharts";

/* ─────────────────────────────────────────────────────────
   DESIGN TOKENS
───────────────────────────────────────────────────────── */
const T = {
  bg:            "#03040a",
  nav:           "#07090f",
  surface:       "#0d1018",
  surfaceRaised: "#121620",
  border:        "rgba(255,255,255,0.055)",
  borderMid:     "rgba(255,255,255,0.11)",
  amber:         "#e6a72a",
  amberDim:      "rgba(230,167,42,0.1)",
  amberBorder:   "rgba(230,167,42,0.28)",
  green:         "#2dd4a0",
  red:           "#f16a6a",
  cyan:          "#5bbfed",
  purple:        "#c084fc",
  orange:        "#f97316",
  blue:          "#60a5fa",
  text:          "#cdd3df",
  muted:         "#5e6573",
  dim:           "#2e333e",
  mono:          "var(--font-mono, 'IBM Plex Mono', 'Courier New', monospace)",
  head:          "var(--font-sans, 'Plus Jakarta Sans', sans-serif)",
  sans:          "var(--font-sans, 'Plus Jakarta Sans', sans-serif)",
};

const SMA_META = {
  sma20:  { label: "SMA 20",  color: T.blue,   short: "20" },
  sma50:  { label: "SMA 50",  color: T.amber,  short: "50" },
  sma100: { label: "SMA 100", color: T.purple, short: "100" },
  sma200: { label: "SMA 200", color: T.orange, short: "200" },
};

/* ─────────────────────────────────────────────────────────
   INDICATOR CALCULATIONS
───────────────────────────────────────────────────────── */
function genOHLC(start, n, end) {
  const data = [];
  let price = start;
  const drift = (end / start) ** (1 / n) - 1;
  for (let i = 0; i < n; i++) {
    const pct = drift + (Math.random() - 0.47) * 0.028;
    const open  = +price.toFixed(2);
    const close = +(price * (1 + pct)).toFixed(2);
    const swing = Math.abs(close - open) * (1 + Math.random());
    const high  = +(Math.max(open, close) + swing * Math.random() * 0.6).toFixed(2);
    const low   = +(Math.min(open, close) - swing * Math.random() * 0.6).toFixed(2);
    const volume = +(22 + Math.random() * 50 + (Math.abs(pct) * 800)).toFixed(1);
    data.push({ i, open, high, low, close, volume });
    price = close;
  }
  return data;
}

function addSMA(data, period) {
  return data.map((d, i) => {
    if (i < period - 1) return d;
    const avg = data.slice(i - period + 1, i + 1).reduce((s, x) => s + x.close, 0) / period;
    return { ...d, [`sma${period}`]: +avg.toFixed(2) };
  });
}

function addBB(data, period = 20, mult = 2) {
  return data.map((d, i) => {
    if (i < period - 1) return d;
    const sl = data.slice(i - period + 1, i + 1).map(x => x.close);
    const mean = sl.reduce((s, x) => s + x, 0) / period;
    const std  = Math.sqrt(sl.reduce((s, x) => s + (x - mean) ** 2, 0) / period);
    return { ...d, bbUpper: +(mean + mult * std).toFixed(2), bbLower: +(mean - mult * std).toFixed(2), bbMid: +mean.toFixed(2) };
  });
}

function addRSI(data, period = 14) {
  const r = data.map((d, i) => ({
    ...d,
    chg: i === 0 ? 0 : +(d.close - data[i - 1].close).toFixed(2),
  }));
  for (let i = period; i < r.length; i++) {
    const sl = r.slice(i - period + 1, i + 1);
    const gain = sl.filter(x => x.chg > 0).reduce((s, x) => s + x.chg, 0) / period;
    const loss = Math.abs(sl.filter(x => x.chg < 0).reduce((s, x) => s + x.chg, 0)) / period;
    r[i].rsi = +(100 - 100 / (1 + (loss === 0 ? 99 : gain / loss))).toFixed(1);
  }
  return r;
}

function addEMA(data, period) {
  const k = 2 / (period + 1);
  const r = data.map(d => ({ ...d }));
  let ema = data.slice(0, period).reduce((s, d) => s + d.close, 0) / period;
  for (let i = period; i < data.length; i++) {
    ema = data[i].close * k + ema * (1 - k);
    r[i][`ema${period}`] = +ema.toFixed(2);
  }
  return r;
}

function addMACD(data) {
  let d = addEMA(data, 12);
  d = addEMA(d, 26);
  for (let i = 26; i < d.length; i++) {
    d[i].macdLine = +((d[i].ema12 || d[i].close) - (d[i].ema26 || d[i].close)).toFixed(2);
  }
  const k9 = 2 / 10;
  let sig = d.slice(26, 35).reduce((s, x) => s + (x.macdLine || 0), 0) / 9;
  for (let i = 35; i < d.length; i++) {
    sig = (d[i].macdLine || 0) * k9 + sig * (1 - k9);
    d[i].macdSig  = +sig.toFixed(2);
    d[i].macdHist = +((d[i].macdLine || 0) - sig).toFixed(2);
  }
  return d;
}

function addStoch(data, period = 14) {
  return data.map((d, i) => {
    if (i < period - 1) return d;
    const sl = data.slice(i - period + 1, i + 1);
    const lo = Math.min(...sl.map(x => x.low));
    const hi = Math.max(...sl.map(x => x.high));
    const k = hi === lo ? 50 : +((d.close - lo) / (hi - lo) * 100).toFixed(1);
    return { ...d, stochK: k };
  });
}

function buildOHLC() {
  let d = genOHLC(398, 150, 1087);
  d = addSMA(d, 20);
  d = addSMA(d, 50);
  d = addSMA(d, 100);
  d = addSMA(d, 200);
  d = addBB(d);
  d = addRSI(d);
  d = addMACD(d);
  d = addStoch(d);
  return d;
}

const OHLC_DATA = buildOHLC();

/* ─────────────────────────────────────────────────────────
   STOCK DATA  — replace with live API calls
───────────────────────────────────────────────────────── */
const STOCK = {
  ticker: "NVDA", company: "NVIDIA Corporation",
  exchange: "NASDAQ", sector: "Semiconductors", country: "USA", founded: 1993,
  price: 1087.45, chg: 23.87, pct: 2.24,
  mktCap: "2.68T", avgVol: "42.3M", beta: "1.73",
  hi52: "1,149", lo52: "398", divYield: "0.03%", eps: "$16.93",
  description:
    "NVIDIA Corporation designs and manufactures graphics processing units (GPUs) and system-on-chip units. The company dominates AI/ML training and inference compute globally. Key platforms: H100/H200/Blackwell Data Center GPUs, GeForce consumer graphics, DRIVE autonomous vehicle compute, and the CUDA software ecosystem.",
  analysts: { sb: 22, b: 12, h: 5, s: 1, ss: 0, target: 1290, lo: 760, hi: 1550 },
  ohlc: OHLC_DATA,
  fundamentals: {
    valuation: [
      { m: "P/E Ratio (TTM)", v: "64.2×", s: "HIGH" },
      { m: "Forward P/E", v: "31.5×", s: "ELEVATED" },
      { m: "PEG Ratio", v: "1.12", s: "FAIR" },
      { m: "Price / Sales", v: "32.4×", s: "HIGH" },
      { m: "Price / Book", v: "41.7×", s: "HIGH" },
      { m: "EV / EBITDA", v: "58.3×", s: "HIGH" },
      { m: "EV / Revenue", v: "34.1×", s: "HIGH" },
    ],
    profitability: [
      { m: "Gross Margin", v: "75.3%", s: "EXCELLENT" },
      { m: "EBITDA Margin", v: "63.8%", s: "EXCELLENT" },
      { m: "Operating Margin", v: "61.9%", s: "EXCELLENT" },
      { m: "Net Margin", v: "55.0%", s: "EXCELLENT" },
      { m: "Return on Equity", v: "119.5%", s: "EXCELLENT" },
      { m: "Return on Assets", v: "47.2%", s: "EXCELLENT" },
      { m: "ROIC", v: "98.3%", s: "EXCELLENT" },
    ],
    growth: [
      { m: "Revenue Growth YoY", v: "+122%", s: "STRONG" },
      { m: "EPS Growth YoY", v: "+581%", s: "STRONG" },
      { m: "FCF Growth YoY", v: "+196%", s: "STRONG" },
      { m: "Revenue Est. FY25", v: "+115%", s: "STRONG" },
      { m: "EPS Estimate FY25", v: "+74%", s: "STRONG" },
    ],
    balance: [
      { m: "Cash & Equivalents", v: "$29.8B", s: "" },
      { m: "Total Debt", v: "$9.8B", s: "" },
      { m: "Net Cash Position", v: "$20.0B", s: "HEALTHY" },
      { m: "Debt / Equity", v: "0.44×", s: "HEALTHY" },
      { m: "Current Ratio", v: "4.17×", s: "HEALTHY" },
      { m: "Free Cash Flow TTM", v: "$53.1B", s: "EXCELLENT" },
    ],
  },
  technicals: {
    oscillators: [
      { m: "RSI (14)", v: "68.3", s: "BULLISH", note: "Near overbought" },
      { m: "MACD (12,26,9)", v: "+12.4", s: "BULLISH", note: "Above signal line" },
      { m: "Stochastic %K", v: "78.2", s: "BULLISH", note: "Above midline" },
      { m: "CCI (20)", v: "+142", s: "BULLISH", note: "Overbought zone" },
      { m: "Williams %R", v: "−22.1", s: "NEUTRAL", note: "Near overbought" },
      { m: "ADX (14)", v: "34.7", s: "BULLISH", note: "Strong trend" },
    ],
    ma: [
      { m: "SMA 20", v: "$1,043", s: "ABOVE", note: "Price above ↑" },
      { m: "SMA 50", v: "$979",   s: "ABOVE", note: "Price above ↑" },
      { m: "SMA 100", v: "$882",  s: "ABOVE", note: "Price above ↑" },
      { m: "SMA 200", v: "$717",  s: "ABOVE", note: "Price above ↑" },
      { m: "EMA 20", v: "$1,052", s: "ABOVE", note: "Price above ↑" },
      { m: "BB Upper", v: "$1,143", s: "NEUTRAL", note: "Below upper band" },
      { m: "BB Lower", v: "$944",   s: "ABOVE",  note: "Price above ↑" },
    ],
    levels: [
      { type: "R", label: "RESISTANCE", level: "$1,150", strength: "STRONG" },
      { type: "R", label: "RESISTANCE", level: "$1,100", strength: "MODERATE" },
      { type: "C", label: "CURRENT",    level: "$1,087", strength: "" },
      { type: "S", label: "SUPPORT",    level: "$1,050", strength: "MODERATE" },
      { type: "S", label: "SUPPORT",    level: "$980",   strength: "STRONG" },
      { type: "S", label: "SUPPORT",    level: "$850",   strength: "MAJOR" },
    ],
  },
  news: [
    { title: "NVIDIA Reports Record Q1 FY2025 Revenue of $26.0 Billion", src: "Reuters", ago: "2h",  s: "POS", body: "Net income surged 628% year-over-year driven by data center GPU demand. Data Center segment alone posted $22.6B, up 427% YoY." },
    { title: "Blackwell GPU Architecture Demand Exceeds Supply Through 2025", src: "Bloomberg", ago: "5h",  s: "POS", body: "Jensen Huang confirmed demand is overwhelming with backlog extending through several more quarters." },
    { title: "EU Regulators Launch Probe into NVIDIA AI Chip Dominance", src: "FT", ago: "1d",  s: "NEG", body: "European antitrust authorities investigating NVIDIA's pricing and bundling practices for AI inference hardware." },
    { title: "NVIDIA & SoftBank Partner on Japan Sovereign AI Infrastructure", src: "Nikkei", ago: "1d",  s: "POS", body: "Strategic deal to build Japan's national AI infrastructure using NVIDIA chips and software stack." },
    { title: "Wall Street Raises Price Targets After NVDA Earnings Beat", src: "Barron's", ago: "2d",  s: "POS", body: "Multiple bulge-bracket banks updated targets; bull case now exceeds $1,500/share on AI compute TAM expansion." },
    { title: "NVIDIA Replaces Intel on Dow Jones Industrial Average", src: "WSJ", ago: "3d",  s: "POS", body: "NVDA joins the DJIA, marking the semiconductor industry's definitive pivot toward AI-centric compute." },
  ],
  peers: [
    { t: "NVDA", n: "NVIDIA",          p: 1087.45, c:  2.24, mc: "2.68T", pe:  64.2, gm: 75.3, rev: "$88.8B" },
    { t: "AMD",  n: "Adv. Micro Dev.", p:  171.32, c: -0.82, mc:  "277B", pe: 195.1, gm: 47.3, rev: "$22.7B" },
    { t: "INTC", n: "Intel",           p:   29.87, c: -1.20, mc:  "127B", pe:  72.4, gm: 40.9, rev: "$54.2B" },
    { t: "AVGO", n: "Broadcom",        p: 1632.10, c:  1.01, mc:  "792B", pe:  48.3, gm: 69.2, rev: "$35.8B" },
    { t: "QCOM", n: "Qualcomm",        p:  193.45, c:  0.55, mc:  "217B", pe:  22.7, gm: 56.7, rev: "$35.8B" },
    { t: "TSM",  n: "TSMC ADR",        p:  175.23, c:  1.88, mc:  "910B", pe:  28.5, gm: 53.4, rev: "$69.3B" },
    { t: "ASML", n: "ASML Holding",    p:  898.40, c:  0.91, mc:  "353B", pe:  50.1, gm: 51.5, rev: "$27.6B" },
  ],
};

/* ─────────────────────────────────────────────────────────
   SHARED HELPERS
───────────────────────────────────────────────────────── */
const SIG = {
  EXCELLENT: T.green, STRONG: T.green, BULLISH: T.green, HEALTHY: T.cyan, ABOVE: T.cyan,
  FAIR: T.cyan, POS: T.green, HIGH: "#f0954c", ELEVATED: "#f0954c", MODERATE: "#f0954c",
  NEUTRAL: T.muted, BEARISH: T.red, NEG: T.red, WEAK: T.red, MAJOR: T.red,
};
const sc = s => SIG[s] || T.muted;

const Badge = ({ s, label }) => {
  const c = sc(s);
  return (
    <span style={{ fontSize: 10, fontFamily: T.mono, fontWeight: 700, color: c,
      background: c + "1a", border: `1px solid ${c}44`, padding: "1px 6px",
      borderRadius: 3, letterSpacing: "0.04em", whiteSpace: "nowrap" }}>
      {label || s}
    </span>
  );
};

const MetRow = ({ m, v, s, note }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "7px 13px", borderBottom: `1px solid ${T.border}` }}>
    <span style={{ fontSize: 12, color: T.muted, fontFamily: T.mono }}>{m}</span>
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {note && <span style={{ fontSize: 11, color: T.dim, fontFamily: T.mono }}>{note}</span>}
      <span style={{ fontSize: 13, color: T.text, fontFamily: T.mono, fontWeight: 700 }}>{v}</span>
      {s && <Badge s={s} />}
    </div>
  </div>
);

const Panel = ({ title, children, style }) => (
  <div style={{ border: `1px solid ${T.border}`, borderRadius: 8, overflow: "hidden", ...style }}>
    <div style={{ padding: "7px 13px", background: "rgba(255,255,255,0.022)", borderBottom: `1px solid ${T.border}` }}>
      <span style={{ fontSize: 10, fontFamily: T.head, fontWeight: 700,
        color: T.amber, letterSpacing: "0.04em", textTransform: "uppercase" }}>{title}</span>
    </div>
    {children}
  </div>
);

const KPICard = ({ label, value }) => (
  <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 6, padding: "10px 13px" }}>
    <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, letterSpacing: "0.04em", marginBottom: 5 }}>{label}</div>
    <div style={{ fontSize: 15, color: T.text, fontFamily: T.mono, fontWeight: 700 }}>{value}</div>
  </div>
);

const tipStyle = { background: T.surfaceRaised, border: `1px solid ${T.border}`,
  borderRadius: 6, fontSize: 11, fontFamily: T.mono, color: T.text };

/* ─────────────────────────────────────────────────────────
   CANDLESTICK SVG CHART
───────────────────────────────────────────────────────── */
function CandleChart({ ohlc, showBB, showSMA }) {
  const ref  = useRef(null);
  const [w, setW] = useState(780);
  useEffect(() => {
    const obs = new ResizeObserver(e => setW(e[0]?.contentRect.width || 780));
    if (ref.current) obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);

  const H = 230;
  const PAD = { l: 58, r: 52, t: 12, b: 22 };
  const cW = w - PAD.l - PAD.r;
  const cH = H - PAD.t - PAD.b;

  const smaKeys = Object.entries(showSMA).filter(([, v]) => v).map(([k]) => k);

  const allP = ohlc.flatMap(d => {
    const pts = [d.low, d.high];
    smaKeys.forEach(k => { if (d[k] != null) pts.push(d[k]); });
    if (showBB) { if (d.bbUpper) pts.push(d.bbUpper); if (d.bbLower) pts.push(d.bbLower); }
    return pts;
  });
  const lo = Math.min(...allP) * 0.996;
  const hi = Math.max(...allP) * 1.004;

  const xOf = i => PAD.l + (i / Math.max(ohlc.length - 1, 1)) * cW;
  const yOf = v => PAD.t + cH - ((v - lo) / (hi - lo)) * cH;
  const cw  = Math.max(2, cW / ohlc.length * 0.52);

  const nTicks = 5;
  const yTicks = Array.from({ length: nTicks }, (_, i) => lo + (hi - lo) * i / (nTicks - 1));

  const pStr = key => ohlc.reduce((p, d, i) => {
    if (d[key] == null) return p;
    return p + (p ? "L" : "M") + `${xOf(i).toFixed(1)},${yOf(d[key]).toFixed(1)}`;
  }, "");

  const bbFill = (() => {
    const up = ohlc.reduce((p, d, i) => d.bbUpper != null
      ? p + (p ? "L" : "M") + `${xOf(i).toFixed(1)},${yOf(d.bbUpper).toFixed(1)}` : p, "");
    const dn = ohlc.reduceRight((p, d, i) => d.bbLower != null
      ? p + "L" + `${xOf(i).toFixed(1)},${yOf(d.bbLower).toFixed(1)}` : p, "");
    return up && dn ? up + dn + "Z" : "";
  })();

  const lastClose = ohlc[ohlc.length - 1]?.close;

  return (
    <div ref={ref} style={{ width: "100%", overflow: "hidden", padding: "10px 0 6px" }}>
      <svg width={w} height={H} style={{ display: "block" }}>
        {/* Y-axis grid + labels */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={PAD.l + cW} y1={yOf(t)} y2={yOf(t)}
              stroke={T.border} strokeWidth={0.5} />
            <text x={PAD.l - 6} y={yOf(t) + 4} textAnchor="end"
              fontSize={10} fill={T.muted} fontFamily={T.mono}>
              ${t >= 1000 ? (t / 1000).toFixed(1) + "k" : t.toFixed(0)}
            </text>
          </g>
        ))}

        {/* BB fill */}
        {showBB && bbFill && <path d={bbFill} fill={`${T.cyan}12`} />}

        {/* BB lines */}
        {showBB && ["bbUpper", "bbLower"].map(k => (
          <path key={k} d={pStr(k)} fill="none" stroke={T.cyan}
            strokeWidth={1} strokeDasharray="4,3" opacity={0.45} />
        ))}

        {/* SMA lines */}
        {smaKeys.map(k => (
          <path key={k} d={pStr(k)} fill="none"
            stroke={SMA_META[k]?.color || T.amber} strokeWidth={1.7} opacity={0.88} />
        ))}

        {/* Candles */}
        {ohlc.map((d, i) => {
          const up   = d.close >= d.open;
          const c    = up ? T.green : T.red;
          const cx   = xOf(i);
          const bT   = yOf(Math.max(d.open, d.close));
          const bB   = yOf(Math.min(d.open, d.close));
          const bH   = Math.max(1, bB - bT);
          return (
            <g key={i}>
              <line x1={cx} y1={yOf(d.high)} x2={cx} y2={yOf(d.low)}
                stroke={c} strokeWidth={1} opacity={0.6} />
              <rect x={cx - cw / 2} y={bT} width={cw} height={bH}
                fill={c} opacity={0.82} rx={0.5} />
            </g>
          );
        })}

        {/* Current price dashed line + label */}
        {lastClose && (
          <>
            <line x1={PAD.l} x2={PAD.l + cW} y1={yOf(lastClose)} y2={yOf(lastClose)}
              stroke={T.amber} strokeWidth={0.85} strokeDasharray="4,3" opacity={0.75} />
            <rect x={PAD.l + cW + 4} y={yOf(lastClose) - 9} width={44} height={18}
              fill={T.amberDim} stroke={T.amberBorder} strokeWidth={0.5} rx={3} />
            <text x={PAD.l + cW + 8} y={yOf(lastClose) + 4} fontSize={10}
              fill={T.amber} fontFamily={T.mono} fontWeight="bold">
              ${lastClose.toFixed(0)}
            </text>
          </>
        )}
      </svg>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   BOX PLOT SVG
───────────────────────────────────────────────────────── */
function BoxPlot({ ohlc, currentPrice }) {
  const closes = ohlc.map(d => d.close).sort((a, b) => a - b);
  const n  = closes.length;
  const mn = closes[0];
  const q1 = closes[Math.floor(n * 0.25)];
  const md = closes[Math.floor(n * 0.5)];
  const q3 = closes[Math.floor(n * 0.75)];
  const mx = closes[n - 1];

  const ref = useRef(null);
  const [w, setW] = useState(700);
  useEffect(() => {
    const obs = new ResizeObserver(e => setW(e[0]?.contentRect.width || 700));
    if (ref.current) obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);

  const H = 90;
  const PAD = { l: 64, r: 64, t: 28, b: 32 };
  const cW = w - PAD.l - PAD.r;
  const midY = PAD.t + (H - PAD.t - PAD.b) / 2;

  const xOf = v => PAD.l + ((v - mn) / (mx - mn)) * cW;

  const ticks = [mn, q1, md, q3, mx];
  const labels = ["Min", "Q1", "Median", "Q3", "Max"];
  const values = [mn, q1, md, q3, mx];

  return (
    <div ref={ref} style={{ width: "100%", padding: "8px 0 4px" }}>
      <svg width={w} height={H} style={{ display: "block" }}>
        {/* Whisker line */}
        <line x1={xOf(mn)} x2={xOf(mx)} y1={midY} y2={midY}
          stroke={T.borderMid} strokeWidth={1.5} />
        {/* Min/Max caps */}
        {[mn, mx].map((v, i) => (
          <line key={i} x1={xOf(v)} x2={xOf(v)} y1={midY - 8} y2={midY + 8}
            stroke={T.muted} strokeWidth={1.5} />
        ))}
        {/* IQR box */}
        <rect x={xOf(q1)} y={midY - 10} width={xOf(q3) - xOf(q1)} height={20}
          fill={T.amberDim} stroke={T.amberBorder} strokeWidth={1} rx={2} />
        {/* Median line */}
        <line x1={xOf(md)} x2={xOf(md)} y1={midY - 11} y2={midY + 11}
          stroke={T.amber} strokeWidth={2} />
        {/* Current price marker */}
        <circle cx={xOf(Math.min(Math.max(currentPrice, mn), mx))} cy={midY}
          r={5} fill={T.green} opacity={0.9} />
        <line x1={xOf(Math.min(Math.max(currentPrice, mn), mx))}
          x2={xOf(Math.min(Math.max(currentPrice, mn), mx))}
          y1={midY - 16} y2={midY - 6} stroke={T.green} strokeWidth={1.5} />

        {/* Labels */}
        {ticks.map((v, i) => (
          <g key={i}>
            <text x={xOf(v)} y={H - 4} textAnchor="middle"
              fontSize={10} fill={T.muted} fontFamily={T.mono}>
              {labels[i]}
            </text>
            <text x={xOf(v)} y={PAD.t - 6} textAnchor="middle"
              fontSize={10} fill={T.text} fontFamily={T.mono} fontWeight="bold">
              ${v >= 1000 ? (v / 1000).toFixed(2) + "k" : v.toFixed(0)}
            </text>
          </g>
        ))}

        {/* Current price label */}
        <text x={xOf(Math.min(Math.max(currentPrice, mn), mx))} y={midY - 20}
          textAnchor="middle" fontSize={10} fill={T.green} fontFamily={T.mono} fontWeight="bold">
          ▲ ${currentPrice.toFixed(0)}
        </text>
      </svg>

      {/* Legend row */}
      <div style={{ display: "flex", gap: 18, padding: "6px 16px 4px", flexWrap: "wrap" }}>
        {[
          { color: T.amber, label: "IQR Box (Q1–Q3)" },
          { color: T.amber, label: `Median  $${md.toFixed(0)}`, fw: 700 },
          { color: T.green, label: `▲ Current  $${currentPrice.toFixed(2)}` },
          { color: T.muted, label: `52W Range  $${mn.toFixed(0)} – $${mx.toFixed(0)}` },
        ].map(({ color, label, fw }, i) => (
          <span key={i} style={{ fontSize: 11, color, fontFamily: T.mono, fontWeight: fw || 400 }}>{label}</span>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   TAB: OVERVIEW
───────────────────────────────────────────────────────── */
const RANGE_LENS = { "1W": 7, "1M": 21, "3M": 63, "1Y": 120 };

function OverviewTab({ s }) {
  const [rng, setRng]   = useState("3M");
  const [smaSel, setSma] = useState({ sma20: true, sma50: true, sma100: false });

  const raw  = s.ohlc.slice(-RANGE_LENS[rng]);
  const data = raw.map(d => ({ ...d, p: d.close }));
  const lo   = Math.min(...data.map(d => d.low));
  const hi   = Math.max(...data.map(d => d.high));
  const up   = data[data.length - 1]?.close >= data[0]?.close;
  const lineC = up ? T.green : T.red;

  const total = s.analysts.sb + s.analysts.b + s.analysts.h + s.analysts.s + s.analysts.ss;
  const ratingBars = [
    { l: "S.Buy", n: s.analysts.sb, c: T.green },
    { l: "Buy",   n: s.analysts.b,  c: "#6ee7b7" },
    { l: "Hold",  n: s.analysts.h,  c: T.amber },
    { l: "Sell",  n: s.analysts.s,  c: T.red },
    { l: "S.Sell",n: s.analysts.ss, c: "#fca5a5" },
  ];
  const maxN = Math.max(...ratingBars.map(r => r.n));
  const smaKeys = Object.entries(smaSel).filter(([, v]) => v).map(([k]) => k);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: 14 }}>
        {/* Price Chart with SMA overlay */}
        <Panel title={`price chart — ${rng}`}>
          <div style={{ padding: "10px 13px 6px" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
              {/* Range buttons */}
              {Object.keys(RANGE_LENS).map(r => (
                <button key={r} onClick={() => setRng(r)} style={{
                  fontSize: 11, fontFamily: T.mono, cursor: "pointer",
                  border: `1px solid ${r === rng ? T.amber : "transparent"}`,
                  background: r === rng ? T.amberDim : "transparent",
                  color: r === rng ? T.amber : T.muted,
                  padding: "2px 9px", borderRadius: 3,
                }}>{r}</button>
              ))}
              <div style={{ width: 1, height: 14, background: T.border }} />
              {/* SMA toggles */}
              {Object.entries(SMA_META).slice(0, 3).map(([key, meta]) => (
                <button key={key} onClick={() => setSma(p => ({ ...p, [key]: !p[key] }))} style={{
                  fontSize: 11, fontFamily: T.mono, cursor: "pointer",
                  border: `1px solid ${smaSel[key] ? meta.color + "80" : "transparent"}`,
                  background: smaSel[key] ? meta.color + "18" : "transparent",
                  color: smaSel[key] ? meta.color : T.dim,
                  padding: "2px 8px", borderRadius: 3,
                }}>SMA{meta.short}</button>
              ))}
            </div>
            <ResponsiveContainer width="100%" height={162}>
              <ComposedChart data={data} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
                <defs>
                  <linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={lineC} stopOpacity={0.18} />
                    <stop offset="100%" stopColor={lineC} stopOpacity={0.01} />
                  </linearGradient>
                </defs>
                <YAxis domain={[lo * 0.975, hi * 1.025]} hide />
                <XAxis dataKey="i" hide />
                <Tooltip contentStyle={tipStyle}
                  formatter={(v, name) => [`$${(+v).toFixed(2)}`, name === "p" ? "Price" : name.toUpperCase()]}
                  labelFormatter={() => ""} />
                <Area type="monotone" dataKey="p" stroke={lineC} strokeWidth={1.6}
                  fill="url(#ag)" dot={false} name="price" />
                {smaKeys.map(k => (
                  <Line key={k} type="monotone" dataKey={k} stroke={SMA_META[k].color}
                    strokeWidth={1.5} dot={false} connectNulls name={k} />
                ))}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        {/* Analyst Consensus */}
        <Panel title="Wall St. Consensus">
          <div style={{ padding: 13 }}>
            <div style={{ textAlign: "center", marginBottom: 12 }}>
              <div style={{ fontFamily: T.head, fontWeight: 700, fontSize: 18, color: T.green, letterSpacing: "0.01em" }}>STRONG BUY</div>
              <div style={{ fontSize: 11, color: T.muted, fontFamily: T.mono, marginTop: 2 }}>{total} analysts covering</div>
            </div>
            <div style={{ display: "flex", gap: 5, alignItems: "flex-end", height: 60, marginBottom: 8 }}>
              {ratingBars.map(({ l, n, c }) => (
                <div key={l} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                  <div style={{ width: "100%", background: c, borderRadius: "2px 2px 0 0", opacity: 0.85, height: n === 0 ? 2 : Math.max(5, (n / maxN) * 52) }} />
                  <span style={{ fontSize: 10, color: T.muted, fontFamily: T.mono }}>{n}</span>
                </div>
              ))}
            </div>
            <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: 10 }}>
              <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 4 }}>12-MONTH PRICE TARGET</div>
              <div style={{ fontSize: 22, fontFamily: T.mono, fontWeight: 700, color: T.amber }}>${s.analysts.target}</div>
              <div style={{ fontSize: 11, color: T.muted, fontFamily: T.mono, marginTop: 2 }}>Low ${s.analysts.lo}  ·  High ${s.analysts.hi}</div>
            </div>
          </div>
        </Panel>
      </div>

      {/* KPI Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
        {[["MARKET CAP", s.mktCap], ["P/E (TTM)", "64.2×"], ["EPS (TTM)", s.eps],
          ["52W HIGH", `$${s.hi52}`], ["52W LOW", `$${s.lo52}`], ["AVG VOLUME", s.avgVol],
          ["DIVIDEND YIELD", s.divYield], ["BETA", s.beta]].map(([l, v]) => (
          <KPICard key={l} label={l} value={v} />
        ))}
      </div>

      <Panel title="Company Overview">
        <div style={{ padding: 13 }}>
          <p style={{ fontSize: 12, color: T.muted, lineHeight: 1.78, margin: 0 }}>{s.description}</p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 22, marginTop: 12 }}>
            {[["SECTOR", s.sector], ["EXCHANGE", s.exchange], ["COUNTRY", s.country], ["FOUNDED", s.founded]].map(([l, v]) => (
              <div key={l}>
                <div style={{ fontSize: 10, color: T.dim, fontFamily: T.mono }}>{l}</div>
                <div style={{ fontSize: 12, color: T.text, fontFamily: T.mono, marginTop: 2 }}>{v}</div>
              </div>
            ))}
          </div>
        </div>
      </Panel>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   TAB: FUNDAMENTALS
───────────────────────────────────────────────────────── */
function FundamentalsTab({ s }) {
  const f = s.fundamentals;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
      <Panel title="Valuation">{f.valuation.map((r, i) => <MetRow key={i} {...r} />)}</Panel>
      <Panel title="Profitability">{f.profitability.map((r, i) => <MetRow key={i} {...r} />)}</Panel>
      <Panel title="Growth Metrics">{f.growth.map((r, i) => <MetRow key={i} {...r} />)}</Panel>
      <Panel title="Balance Sheet">{f.balance.map((r, i) => <MetRow key={i} {...r} />)}</Panel>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   TAB: TECHNICALS
───────────────────────────────────────────────────────── */
function TechnicalsTab({ s }) {
  const osc  = s.technicals.oscillators;
  const bull = osc.filter(x => x.s === "BULLISH").length;
  const pct  = Math.round((bull / osc.length) * 100);
  const sig  = pct >= 75 ? { l: "STRONG BULLISH", c: T.green } : pct >= 50 ? { l: "BULLISH", c: "#6ee7b7" } : { l: "BEARISH", c: T.red };
  const lvlC = type => ({ R: T.red, C: T.amber, S: T.green }[type]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ border: `1px solid ${T.amberBorder}`, background: "rgba(230,167,42,0.06)", borderRadius: 8, padding: "14px 16px", display: "flex", alignItems: "center", gap: 24 }}>
        <div style={{ minWidth: 170 }}>
          <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 3 }}>OVERALL SIGNAL</div>
          <div style={{ fontFamily: T.head, fontWeight: 700, fontSize: 20, color: sig.c, letterSpacing: "0.01em" }}>{sig.l}</div>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", gap: 3, marginBottom: 6 }}>
            {osc.map((x, i) => <div key={i} style={{ flex: 1, height: 6, background: sc(x.s), borderRadius: 2, opacity: 0.8 }} />)}
          </div>
          <div style={{ fontSize: 11, color: T.muted, fontFamily: T.mono }}>{bull} bullish · {osc.length - bull} neutral / bearish of {osc.length} oscillators</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 30, fontFamily: T.mono, fontWeight: 700, color: sig.c }}>{pct}%</div>
          <div style={{ fontSize: 11, color: T.muted, fontFamily: T.mono }}>bullish</div>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <Panel title="Momentum Oscillators">{osc.map((r, i) => <MetRow key={i} m={r.m} v={r.v} s={r.s} note={r.note} />)}</Panel>
        <Panel title="Moving Averages">{s.technicals.ma.map((r, i) => <MetRow key={i} m={r.m} v={r.v} s={r.s} note={r.note} />)}</Panel>
      </div>
      <Panel title="Key Price Levels — Support & Resistance">
        {s.technicals.levels.map((lvl, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", padding: "8px 13px", borderBottom: `1px solid ${T.border}`, background: lvl.type === "C" ? "rgba(230,167,42,0.06)" : "transparent" }}>
            <span style={{ fontSize: 10, fontFamily: T.mono, fontWeight: 700, color: lvlC(lvl.type), minWidth: 96 }}>{lvl.label}</span>
            <div style={{ flex: 1, height: 1, background: lvl.type === "C" ? T.amber + "55" : T.border, margin: "0 14px" }} />
            <span style={{ fontSize: 14, fontFamily: T.mono, fontWeight: 700, color: T.text, marginRight: 10 }}>{lvl.level}</span>
            {lvl.strength && <Badge s={lvl.strength} />}
          </div>
        ))}
      </Panel>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   TAB: CHARTS  (candlestick + indicators + box plot)
───────────────────────────────────────────────────────── */
function ChartsTab({ s }) {
  const [showSMA, setShowSMA] = useState({ sma20: true, sma50: true, sma100: true, sma200: false });
  const [showBB,  setShowBB]  = useState(true);
  const [candleN, setCandleN] = useState(90);

  const ohlc      = s.ohlc;
  const candles   = ohlc.slice(-candleN);
  const rsiData   = ohlc.filter(d => d.rsi != null).slice(-60);
  const macdData  = ohlc.filter(d => d.macdLine != null).slice(-60);
  const stochData = ohlc.filter(d => d.stochK != null).slice(-60);
  const volData   = candles.map(d => ({ ...d, color: d.close >= d.open ? T.green : T.red }));

  const smaActiveKeys = Object.entries(showSMA).filter(([, v]) => v).map(([k]) => k);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>

      {/* ── Indicator Toggle Bar ── */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", padding: "8px 13px", background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8 }}>
        <span style={{ fontSize: 11, color: T.muted, fontFamily: T.mono, marginRight: 4 }}>OVERLAYS:</span>
        {Object.entries(SMA_META).map(([key, meta]) => (
          <button key={key} onClick={() => setShowSMA(p => ({ ...p, [key]: !p[key] }))} style={{
            fontSize: 11, fontFamily: T.mono, cursor: "pointer",
            border: `1px solid ${showSMA[key] ? meta.color + "80" : T.border}`,
            background: showSMA[key] ? meta.color + "18" : "transparent",
            color: showSMA[key] ? meta.color : T.dim,
            padding: "3px 10px", borderRadius: 4,
          }}>{meta.label}</button>
        ))}
        <button onClick={() => setShowBB(p => !p)} style={{
          fontSize: 11, fontFamily: T.mono, cursor: "pointer",
          border: `1px solid ${showBB ? T.cyan + "80" : T.border}`,
          background: showBB ? T.cyan + "18" : "transparent",
          color: showBB ? T.cyan : T.dim,
          padding: "3px 10px", borderRadius: 4,
        }}>Bollinger Bands</button>
        <div style={{ marginLeft: "auto", display: "flex", gap: 5 }}>
          {[30, 60, 90, 120].map(n => (
            <button key={n} onClick={() => setCandleN(n)} style={{
              fontSize: 11, fontFamily: T.mono, cursor: "pointer",
              border: `1px solid ${candleN === n ? T.amber : "transparent"}`,
              background: candleN === n ? T.amberDim : "transparent",
              color: candleN === n ? T.amber : T.muted,
              padding: "3px 8px", borderRadius: 3,
            }}>{n}D</button>
          ))}
        </div>
      </div>

      {/* SMA Legend */}
      {smaActiveKeys.length > 0 && (
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", padding: "0 4px" }}>
          {smaActiveKeys.map(k => (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <div style={{ width: 20, height: 2.5, background: SMA_META[k].color, borderRadius: 2 }} />
              <span style={{ fontSize: 11, color: SMA_META[k].color, fontFamily: T.mono }}>{SMA_META[k].label}</span>
            </div>
          ))}
          {showBB && (
            <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <div style={{ width: 20, height: 1.5, background: T.cyan, borderRadius: 2, opacity: 0.5 }} />
              <span style={{ fontSize: 11, color: T.cyan, fontFamily: T.mono, opacity: 0.7 }}>Bollinger Bands (20,2)</span>
            </div>
          )}
        </div>
      )}

      {/* ── Candlestick Chart ── */}
      <Panel title={`Candlestick — OHLC · ${candleN}-Day View`}>
        <CandleChart ohlc={candles} showBB={showBB} showSMA={showSMA} />
      </Panel>

      {/* ── Volume ── */}
      <Panel title="Volume">
        <div style={{ padding: "8px 4px 4px" }}>
          <ResponsiveContainer width="100%" height={72}>
            <ComposedChart data={volData} margin={{ top: 2, right: 4, bottom: 0, left: 4 }}>
              <YAxis hide />
              <XAxis dataKey="i" hide />
              <Tooltip contentStyle={tipStyle}
                formatter={v => [`${(+v).toFixed(1)}M`, "Volume"]}
                labelFormatter={() => ""} />
              <Bar dataKey="volume" radius={[1, 1, 0, 0]}>
                {volData.map((d, i) => <Cell key={i} fill={d.close >= d.open ? T.green : T.red} opacity={0.65} />)}
              </Bar>
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      {/* ── RSI + Stochastic ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <Panel title="RSI (14) — Relative Strength Index">
          <div style={{ padding: "8px 4px 4px" }}>
            <ResponsiveContainer width="100%" height={110}>
              <ComposedChart data={rsiData} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
                <YAxis domain={[0, 100]} hide />
                <XAxis dataKey="i" hide />
                <Tooltip contentStyle={tipStyle}
                  formatter={v => [`${(+v).toFixed(1)}`, "RSI"]}
                  labelFormatter={() => ""} />
                <ReferenceLine y={70} stroke={T.red} strokeDasharray="3,3" strokeWidth={0.8} opacity={0.6} />
                <ReferenceLine y={50} stroke={T.dim} strokeDasharray="3,3" strokeWidth={0.6} opacity={0.5} />
                <ReferenceLine y={30} stroke={T.green} strokeDasharray="3,3" strokeWidth={0.8} opacity={0.6} />
                <Line type="monotone" dataKey="rsi" stroke={T.blue} strokeWidth={1.6} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
            <div style={{ display: "flex", justifyContent: "space-between", padding: "0 12px 6px", fontSize: 10, fontFamily: T.mono }}>
              <span style={{ color: T.green }}>30 — Oversold</span>
              <span style={{ color: T.muted }}>50 — Neutral</span>
              <span style={{ color: T.red }}>70 — Overbought</span>
            </div>
          </div>
        </Panel>

        <Panel title="Stochastic Oscillator %K (14)">
          <div style={{ padding: "8px 4px 4px" }}>
            <ResponsiveContainer width="100%" height={110}>
              <ComposedChart data={stochData} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
                <YAxis domain={[0, 100]} hide />
                <XAxis dataKey="i" hide />
                <Tooltip contentStyle={tipStyle}
                  formatter={v => [`${(+v).toFixed(1)}`, "Stoch %K"]}
                  labelFormatter={() => ""} />
                <ReferenceLine y={80} stroke={T.red} strokeDasharray="3,3" strokeWidth={0.8} opacity={0.6} />
                <ReferenceLine y={50} stroke={T.dim} strokeDasharray="3,3" strokeWidth={0.6} opacity={0.5} />
                <ReferenceLine y={20} stroke={T.green} strokeDasharray="3,3" strokeWidth={0.8} opacity={0.6} />
                <Line type="monotone" dataKey="stochK" stroke={T.purple} strokeWidth={1.6} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
            <div style={{ display: "flex", justifyContent: "space-between", padding: "0 12px 6px", fontSize: 10, fontFamily: T.mono }}>
              <span style={{ color: T.green }}>20 — Oversold</span>
              <span style={{ color: T.muted }}>50 — Neutral</span>
              <span style={{ color: T.red }}>80 — Overbought</span>
            </div>
          </div>
        </Panel>
      </div>

      {/* ── MACD ── */}
      <Panel title="MACD (12, 26, 9) — Moving Average Convergence Divergence">
        <div style={{ padding: "8px 4px 4px" }}>
          <ResponsiveContainer width="100%" height={120}>
            <ComposedChart data={macdData} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
              <YAxis hide />
              <XAxis dataKey="i" hide />
              <ReferenceLine y={0} stroke={T.borderMid} strokeWidth={1} />
              <Tooltip contentStyle={tipStyle}
                formatter={(v, name) => [`${(+v).toFixed(2)}`, name === "macdHist" ? "Histogram" : name === "macdLine" ? "MACD" : "Signal"]}
                labelFormatter={() => ""} />
              <Bar dataKey="macdHist" radius={[1, 1, 0, 0]}>
                {macdData.map((d, i) => (
                  <Cell key={i} fill={d.macdHist >= 0 ? T.green : T.red} opacity={0.55} />
                ))}
              </Bar>
              <Line type="monotone" dataKey="macdLine" stroke={T.blue} strokeWidth={1.6} dot={false} connectNulls name="macdLine" />
              <Line type="monotone" dataKey="macdSig" stroke={T.orange} strokeWidth={1.4} dot={false} connectNulls name="macdSig" strokeDasharray="4,2" />
            </ComposedChart>
          </ResponsiveContainer>
          <div style={{ display: "flex", gap: 18, padding: "4px 12px 6px", fontSize: 11, fontFamily: T.mono }}>
            <span style={{ color: T.blue }}>— MACD Line</span>
            <span style={{ color: T.orange }}>- - Signal (9 EMA)</span>
            <span style={{ color: T.green }}>█ Histogram (+)</span>
            <span style={{ color: T.red }}>█ Histogram (−)</span>
          </div>
        </div>
      </Panel>

      {/* ── Box Plot ── */}
      <Panel title={`52-Week Price Distribution — Box & Whisker Plot (${ohlc.length} sessions)`}>
        <BoxPlot ohlc={ohlc} currentPrice={s.price} />
      </Panel>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   TAB: NEWS
───────────────────────────────────────────────────────── */
const SENT = { POS: "POSITIVE", NEG: "NEGATIVE", NEUTRAL: "NEUTRAL" };

function NewsTab({ s }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {s.news.map((n, i) => (
        <div key={i} style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: "14px 16px", display: "flex", gap: 14 }}>
          <div style={{ width: 4, borderRadius: 2, background: sc(n.s), flexShrink: 0, alignSelf: "stretch", minHeight: 60 }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, color: T.text, fontWeight: 600, lineHeight: 1.42, marginBottom: 6 }}>{n.title}</div>
            <p style={{ fontSize: 12, color: T.muted, margin: "0 0 8px", lineHeight: 1.68 }}>{n.body}</p>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span style={{ fontSize: 11, fontFamily: T.mono, fontWeight: 700, color: T.amber }}>{n.src}</span>
              <span style={{ fontSize: 11, fontFamily: T.mono, color: T.dim }}>{n.ago} ago</span>
              <Badge s={n.s} label={SENT[n.s]} />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   TAB: PEERS
───────────────────────────────────────────────────────── */
function PeersTab({ s }) {
  const hdrs = ["TICKER", "COMPANY", "PRICE", "CHG %", "MKT CAP", "P/E", "GROSS MARGIN", "REVENUE (TTM)"];
  return (
    <Panel title={`Sector Peers — ${s.sector}`}>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ background: "rgba(255,255,255,0.025)", borderBottom: `1px solid ${T.border}` }}>
              {hdrs.map(h => (
                <th key={h} style={{ padding: "9px 13px", textAlign: "left", fontSize: 10,
                  color: T.amber, fontFamily: T.head, fontWeight: 700, letterSpacing: "0.03em", whiteSpace: "nowrap" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {s.peers.map((p, i) => (
              <tr key={i} style={{ borderBottom: `1px solid ${T.border}`, background: p.t === s.ticker ? T.amberDim : "transparent" }}>
                <td style={{ padding: "9px 13px", fontSize: 13, fontFamily: T.mono, color: T.amber, fontWeight: 700 }}>{p.t}</td>
                <td style={{ padding: "9px 13px", fontSize: 12, color: T.text, whiteSpace: "nowrap" }}>{p.n}</td>
                <td style={{ padding: "9px 13px", fontSize: 13, fontFamily: T.mono, color: T.text, fontWeight: 700 }}>${p.p.toLocaleString()}</td>
                <td style={{ padding: "9px 13px", fontSize: 12, fontFamily: T.mono, fontWeight: 700, color: p.c >= 0 ? T.green : T.red, whiteSpace: "nowrap" }}>
                  {p.c >= 0 ? "+" : ""}{p.c.toFixed(2)}%
                </td>
                <td style={{ padding: "9px 13px", fontSize: 12, fontFamily: T.mono, color: T.muted }}>{p.mc}</td>
                <td style={{ padding: "9px 13px", fontSize: 12, fontFamily: T.mono, color: T.muted }}>{p.pe}×</td>
                <td style={{ padding: "9px 13px", fontSize: 12, fontFamily: T.mono, color: T.muted }}>{p.gm}%</td>
                <td style={{ padding: "9px 13px", fontSize: 12, fontFamily: T.mono, color: T.muted }}>{p.rev}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/* ─────────────────────────────────────────────────────────
   TAB: AI ANALYST CHAT
───────────────────────────────────────────────────────── */
const SUGGESTED = [
  "What is the investment thesis for NVDA?",
  "Is NVDA overvalued at these prices?",
  "What are the key risks for NVDA in 2025?",
  "Compare NVDA and AMD fundamentals",
  "Explain NVDA's competitive moat",
];

function ChatTab({ ticker }) {
  const [msgs, setMsgs] = useState([{
    role: "ai",
    text: `Hello! I'm your AI analyst powered by Llama 3.3-70b via Groq. Ask me anything about ${ticker} — valuation, competitive dynamics, risk assessment, earnings catalysts, or your own investment thesis.`,
  }]);
  const [input, setInput] = useState("");
  const [busy,  setBusy]  = useState(false);
  const endRef = useRef(null);
  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);

  const send = async (text) => {
    const msg = (text || input).trim();
    if (!msg || busy) return;
    setInput("");
    setMsgs(p => [...p, { role: "user", text: msg }]);
    setBusy(true);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg, ticker }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const reply = data.response || data.message || data.content || data.text || JSON.stringify(data);
      setMsgs(p => [...p, { role: "ai", text: reply }]);
    } catch (e) {
      setMsgs(p => [...p, { role: "ai", text: `⚠️ Backend not reachable. Update the fetch URL to your /api/chat endpoint.\n\nError: ${e.message}` }]);
    }
    setBusy(false);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: 520, gap: 10 }}>
      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 11, paddingRight: 4 }}>
        {msgs.map((m, i) => (
          <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", gap: 9 }}>
            {m.role === "ai" && (
              <div style={{ width: 28, height: 28, borderRadius: "50%", background: T.amberDim, border: `1px solid ${T.amber}55`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, flexShrink: 0, marginTop: 3 }}>⚡</div>
            )}
            <div style={{ maxWidth: "72%", padding: "10px 14px", borderRadius: 8, fontSize: 13, lineHeight: 1.72,
              background: m.role === "user" ? T.amberDim : T.surface,
              border: `1px solid ${m.role === "user" ? T.amberBorder : T.border}`,
              color: T.text, whiteSpace: "pre-wrap", fontFamily: m.role === "user" ? T.mono : "inherit" }}>
              {m.text}
            </div>
          </div>
        ))}
        {busy && (
          <div style={{ display: "flex", gap: 9 }}>
            <div style={{ width: 28, height: 28, borderRadius: "50%", background: T.amberDim, border: `1px solid ${T.amber}55`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, flexShrink: 0, marginTop: 3 }}>⚡</div>
            <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: "11px 16px", display: "flex", gap: 5, alignItems: "center" }}>
              {[0, 1, 2].map(j => <span key={j} style={{ width: 5, height: 5, borderRadius: "50%", background: T.amber, display: "inline-block", animation: `tdot 1.2s ${j * 0.18}s infinite ease-in-out` }} />)}
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {msgs.length <= 2 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {SUGGESTED.map((s, i) => (
            <button key={i} onClick={() => send(s)} style={{ fontSize: 11, fontFamily: T.mono, color: T.muted, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 20, padding: "5px 11px", cursor: "pointer" }}>{s}</button>
          ))}
        </div>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <input value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && !e.shiftKey && send()}
          placeholder={`Ask the analyst about ${ticker}...`}
          style={{ flex: 1, background: T.surface, border: `1px solid ${T.borderMid}`, borderRadius: 8, padding: "10px 14px", color: T.text, fontSize: 13, fontFamily: T.mono, outline: "none" }} />
        <button onClick={() => send()} disabled={busy || !input.trim()} style={{
          background: busy || !input.trim() ? T.amberDim : T.amber,
          border: "none", borderRadius: 8, padding: "0 20px",
          color: busy || !input.trim() ? T.amber : "#000",
          fontSize: 12, fontFamily: T.mono, fontWeight: 700, cursor: busy ? "not-allowed" : "pointer", transition: "all 0.15s" }}>SEND ↑</button>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   MAIN APP
───────────────────────────────────────────────────────── */
const TABS = [
  { id: "overview",     label: "OVERVIEW"      },
  { id: "fundamentals", label: "FUNDAMENTALS"  },
  { id: "technicals",   label: "TECHNICALS"    },
  { id: "charts",       label: "CHARTS"        },
  { id: "news",         label: "NEWS"          },
  { id: "peers",        label: "PEERS"         },
  { id: "chat",         label: "⚡ AI ANALYST" },
];

export default function AnalystAgent() {
  const [ticker, setTicker] = useState("NVDA");
  const [search, setSearch]  = useState("");
  const [tab, setTab]        = useState("overview");
  const s   = STOCK;
  const pos = s.chg > 0;

  return (
    <div style={{ background: T.bg, minHeight: "100vh", color: T.text }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');
        *{box-sizing:border-box;margin:0;padding:0;}
        body{background:${T.bg}; font-family: var(--font-sans, 'Plus Jakarta Sans', sans-serif);}
        p, span, div, label, td, th {font-family: var(--font-sans, 'Plus Jakarta Sans', sans-serif);}
        ::-webkit-scrollbar{width:4px;height:4px;}
        ::-webkit-scrollbar-track{background:${T.surface};}
        ::-webkit-scrollbar-thumb{background:#2d3748;border-radius:3px;}
        @keyframes tdot{0%,60%,100%{transform:translateY(0);opacity:.4}30%{transform:translateY(-5px);opacity:1}}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
        input::placeholder{color:${T.dim};}
        button{font-family:inherit;}
      `}</style>

      {/* ── TOP NAV ── */}
      <div style={{ background: T.nav, borderBottom: `1px solid ${T.border}`, padding: "0 24px", height: 50, display: "flex", alignItems: "center", gap: 20, position: "sticky", top: 0, zIndex: 100 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          <span style={{ fontFamily: T.head, fontWeight: 700, fontSize: 20, color: T.amber, letterSpacing: "0.01em" }}>ANALYST</span>
          <span style={{ fontFamily: T.head, fontSize: 20, color: T.muted, letterSpacing: "0.01em" }}>AGENT</span>
        </div>
        <div style={{ display: "flex", gap: 6, flex: 1, maxWidth: 380 }}>
          <input value={search} onChange={e => setSearch(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === "Enter" && search.trim() && setTicker(search.trim())}
            placeholder="Search ticker (AAPL, TSLA, NVDA...)"
            style={{ flex: 1, background: "#0f121a", border: `1px solid ${T.border}`, borderRadius: 6, padding: "6px 12px", color: T.text, fontSize: 12, fontFamily: T.mono, outline: "none" }} />
          <button onClick={() => search.trim() && setTicker(search.trim())} style={{
            background: T.amberDim, border: `1px solid ${T.amberBorder}`, borderRadius: 6,
            padding: "6px 13px", color: T.amber, fontSize: 11, fontFamily: T.mono, fontWeight: 700, cursor: "pointer" }}>ANALYZE</button>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 18 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: T.green, display: "inline-block", animation: "pulse 2s infinite" }} />
            <span style={{ fontSize: 11, fontFamily: T.mono, color: T.green }}>MARKET OPEN</span>
          </div>
          <span style={{ fontSize: 11, fontFamily: T.mono, color: T.dim }}>llama-3.3-70b  ·  groq</span>
        </div>
      </div>

      {/* ── HERO BAR ── */}
      <div style={{ background: T.nav, borderBottom: `1px solid ${T.border}`, padding: "13px 24px", display: "flex", alignItems: "center", gap: 30, flexWrap: "wrap" }}>
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 3 }}>
            <span style={{ fontFamily: T.head, fontWeight: 700, fontSize: 28, color: T.amber, letterSpacing: "0.01em" }}>{s.ticker}</span>
            <span style={{ fontSize: 13, color: T.muted }}>{s.company}</span>
          </div>
          <div style={{ fontSize: 11, color: T.dim, fontFamily: T.mono }}>{s.exchange} · {s.sector} · {s.country}</div>
        </div>
        <div style={{ borderLeft: `1px solid ${T.border}`, paddingLeft: 30 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
            <span style={{ fontFamily: T.mono, fontWeight: 700, fontSize: 30, color: T.text }}>${s.price.toLocaleString()}</span>
            <div>
              <div style={{ fontFamily: T.mono, fontWeight: 700, fontSize: 14, color: pos ? T.green : T.red }}>{pos ? "+" : ""}${s.chg.toFixed(2)}</div>
              <div style={{ fontFamily: T.mono, fontSize: 13, color: pos ? T.green : T.red }}>{pos ? "+" : ""}{s.pct.toFixed(2)}%</div>
            </div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 26, borderLeft: `1px solid ${T.border}`, paddingLeft: 30 }}>
          {[["MKT CAP", s.mktCap], ["P/E (TTM)", "64.2×"], ["VOLUME", s.avgVol], ["BETA", s.beta]].map(([l, v]) => (
            <div key={l}>
              <div style={{ fontSize: 10, color: T.dim, fontFamily: T.mono, letterSpacing: "0.01em" }}>{l}</div>
              <div style={{ fontSize: 13, color: T.text, fontFamily: T.mono, fontWeight: 700, marginTop: 2 }}>{v}</div>
            </div>
          ))}
        </div>
        <div style={{ marginLeft: "auto", textAlign: "right" }}>
          <div style={{ fontSize: 10, color: T.muted, fontFamily: T.mono, marginBottom: 3 }}>ANALYST CONSENSUS</div>
          <div style={{ fontFamily: T.head, fontWeight: 700, fontSize: 17, color: T.green, letterSpacing: "0.01em" }}>STRONG BUY</div>
          <div style={{ fontSize: 11, color: T.muted, fontFamily: T.mono }}>
            {s.analysts.sb + s.analysts.b + s.analysts.h + s.analysts.s} analysts · PT ${s.analysts.target}
          </div>
        </div>
      </div>

      {/* ── TAB BAR ── */}
      <div style={{ background: T.nav, borderBottom: `1px solid ${T.border}`, padding: "0 24px", display: "flex" }}>
        {TABS.map(({ id, label }) => (
          <button key={id} onClick={() => setTab(id)} style={{
            padding: "11px 16px", fontSize: 12, fontFamily: T.head, fontWeight: 700,
            letterSpacing: "0.01em", cursor: "pointer", border: "none", outline: "none",
            background: "transparent", color: tab === id ? T.amber : T.muted,
            borderBottom: tab === id ? `2px solid ${T.amber}` : "2px solid transparent",
            transition: "color 0.15s" }}>{label}</button>
        ))}
      </div>

      {/* ── CONTENT ── */}
      <div style={{ padding: "20px 24px 72px", maxWidth: 1120 }}>
        {tab === "overview"     && <OverviewTab     s={s} />}
        {tab === "fundamentals" && <FundamentalsTab s={s} />}
        {tab === "technicals"   && <TechnicalsTab   s={s} />}
        {tab === "charts"       && <ChartsTab        s={s} />}
        {tab === "news"         && <NewsTab          s={s} />}
        {tab === "peers"        && <PeersTab         s={s} />}
        {tab === "chat"         && <ChatTab          ticker={s.ticker} />}
      </div>

      {/* ── STATUS BAR ── */}
      <div style={{ background: "#040408", borderTop: `1px solid ${T.amberBorder}`, padding: "5px 24px", display: "flex", alignItems: "center", position: "fixed", bottom: 0, left: 0, right: 0 }}>
        {[["⚡ ANALYST AGENT", T.amber], ["llama-3.3-70b-versatile via Groq", T.muted], ["Gemini fallback enabled", T.dim], ["Educational purposes only · Not financial advice", T.dim]].map(([text, color], i) => (
          <span key={i} style={{ fontSize: 10, fontFamily: T.mono, color }}>
            {i > 0 && <span style={{ color: T.dim, margin: "0 12px" }}>·</span>}
            {text}
          </span>
        ))}
        <span style={{ marginLeft: "auto", fontSize: 10, fontFamily: T.mono, color: T.dim }}>{new Date().toLocaleTimeString()}</span>
      </div>
    </div>
  );
}
