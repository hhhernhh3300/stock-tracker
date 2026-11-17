// job-scheduler — starts GitHub Actions jobs on time (see ../wrangler.toml).
//
// Times are New York wall-clock on weekdays, so they stay right across daylight
// saving. Every time must be a multiple of 15 minutes (the cron fires every 15).

const OWNER = 'hhhernhh3300'

const PLAN = [
  // Private portfolio: positions first, then the value screen the 8:07am Claude routine reads.
  { at: '06:45', repo: 'portfolio-private', workflow: 'ibkr.yml' },
  { at: '07:15', repo: 'portfolio-private', workflow: 'agent-screen.yml' },
  // Public screener, before the US open.
  { at: '07:00', repo: 'stock-tracker', workflow: 'screen.yml' },
  // Public analyst site: AI providers + data sources.
  { at: '07:30', repo: 'stock-tracker', workflow: 'analyst-health.yml' },
  // Finance-agent health checks (screen ran? routine published?).
  { at: '07:45', repo: 'portfolio-private', workflow: 'agent-health.yml' },
  { at: '08:45', repo: 'portfolio-private', workflow: 'agent-health.yml' },
  { at: '10:45', repo: 'portfolio-private', workflow: 'agent-health.yml' },
  { at: '12:45', repo: 'portfolio-private', workflow: 'agent-health.yml' },
  // After-hours screener, 1.5 h into extended trading.
  { at: '17:30', repo: 'stock-tracker', workflow: 'after_hours.yml' },
]

function newYorkClock(date) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(date)
      .map((p) => [p.type, p.value]),
  )
  return { weekday: parts.weekday, hhmm: `${parts.hour}:${parts.minute}` }
}

async function dispatch(env, job) {
  const url = `https://api.github.com/repos/${OWNER}/${job.repo}/actions/workflows/${job.workflow}/dispatches`
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GH_DISPATCH}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'job-scheduler',
    },
    body: JSON.stringify({ ref: 'main' }),
  })
  // 204 = started. Log the status only — never the token.
  const detail = res.status === 204 ? '' : ` ${(await res.text()).slice(0, 200)}`
  console.log(`${job.repo}/${job.workflow}: HTTP ${res.status}${detail}`)
}

export default {
  async scheduled(event, env, ctx) {
    if (!env.GH_DISPATCH) {
      console.log('GH_DISPATCH secret is not set yet; nothing dispatched.')
      return
    }
    const { weekday, hhmm } = newYorkClock(new Date(event.scheduledTime))
    if (weekday === 'Sat' || weekday === 'Sun') return
    const due = PLAN.filter((job) => job.at === hhmm)
    if (due.length) ctx.waitUntil(Promise.all(due.map((job) => dispatch(env, job))))
  },
}
