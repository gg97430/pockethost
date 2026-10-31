export type MonitorState = 'ok' | 'warning' | 'error' | 'unknown' | 'paused'
export type MonitorGroup = 'infrastructure' | 'databases' | 'hosting' | 'backups' | 'security'
export type MonitorCheck = {
  id: string
  group: MonitorGroup
  name: string
  state: MonitorState
  detail: string
  href?: string
  latencyMs?: number
}
export type MonitoringReport = {
  version: 1
  checkedAt: string
  host: string
  domain: string
  checks: MonitorCheck[]
  counts: Record<MonitorState, number>
  state: MonitorState
}

const states: MonitorState[] = ['ok', 'warning', 'error', 'unknown', 'paused']
export const monitorGroups: MonitorGroup[] = ['infrastructure', 'databases', 'hosting', 'backups', 'security']
export const monitorState = (checks: MonitorCheck[]): MonitorState =>
  (['error', 'warning', 'unknown'] as const).find((state) => checks.some((row) => row.state === state)) || 'ok'

export function monitoringReport(
  host: string,
  domain: string,
  checks: MonitorCheck[],
  now = Date.now()
): MonitoringReport {
  return {
    version: 1,
    checkedAt: new Date(now).toISOString(),
    host,
    domain,
    checks,
    counts: Object.fromEntries(
      states.map((state) => [state, checks.filter((row) => row.state === state).length])
    ) as Record<MonitorState, number>,
    state: monitorState(checks),
  }
}

/** A report crosses the privileged collector → operator dashboard boundary; project data is never trusted as HTML. */
export function parseMonitoringReport(value: unknown): MonitoringReport {
  const raw = value as MonitoringReport
  if (
    !raw ||
    raw.version !== 1 ||
    !Number.isFinite(Date.parse(raw.checkedAt)) ||
    typeof raw.host !== 'string' ||
    raw.host.length > 150 ||
    typeof raw.domain !== 'string' ||
    raw.domain.length > 253 ||
    !Array.isArray(raw.checks) ||
    raw.checks.length > 10000
  )
    throw Error('Rapport de supervision invalide.')
  const checks = raw.checks.map((row): MonitorCheck => {
    if (
      !row ||
      typeof row.id !== 'string' ||
      row.id.length > 150 ||
      !monitorGroups.includes(row.group) ||
      !states.includes(row.state) ||
      typeof row.name !== 'string' ||
      row.name.length > 200 ||
      typeof row.detail !== 'string' ||
      row.detail.length > 600
    )
      throw Error('Contrôle de supervision invalide.')
    const check: MonitorCheck = { id: row.id, group: row.group, state: row.state, name: row.name, detail: row.detail }
    if (
      typeof row.href === 'string' &&
      /^\/(?:instances\/[a-z0-9]{15}|applications\/[a-z0-9]{15})(?:\/[a-z-]+)?$/.test(row.href)
    )
      check.href = row.href
    if (typeof row.latencyMs === 'number' && Number.isFinite(row.latencyMs) && row.latencyMs >= 0)
      check.latencyMs = Math.round(row.latencyMs)
    return check
  })
  return monitoringReport(raw.host, raw.domain, checks, Date.parse(raw.checkedAt))
}

export const monitoringStale = (report: MonitoringReport, now = Date.now()) =>
  now - Date.parse(report.checkedAt) > 180000 || Date.parse(report.checkedAt) > now + 30000

type Signal = { failed: number; healthy: number; active: boolean; name: string; group: MonitorGroup }
export type MonitorSignals = Record<string, Signal>
export function advanceMonitorSignals(checks: MonitorCheck[], previous: MonitorSignals = {}) {
  const signals: MonitorSignals = {},
    opened: MonitorCheck[] = [],
    recovered: string[] = []
  for (const row of checks) {
    const old = previous[row.id]
    const failed = row.state === 'error' || row.state === 'warning'
    const healthy = row.state === 'ok' || row.state === 'paused'
    const signal = {
      failed: failed ? (old?.failed || 0) + 1 : 0,
      healthy: healthy ? (old?.healthy || 0) + 1 : 0,
      active: old?.active || false,
      name: row.name,
      group: row.group,
    }
    if (signal.failed >= 2 && !signal.active) {
      signal.active = true
      opened.push(row)
    }
    if (signal.healthy >= 2 && signal.active) {
      signal.active = false
      recovered.push(row.name)
    }
    signals[row.id] = signal
  }
  // An absent or unknown check does not prove recovery (controller outage, disabled collector, deleted resource).
  let retained = Object.keys(signals).length
  for (const [id, old] of Object.entries(previous))
    if (!signals[id] && old.active && retained < 10000) {
      signals[id] = { ...old, failed: 0, healthy: 0 }
      retained++
    }
  return { signals, opened, recovered }
}
