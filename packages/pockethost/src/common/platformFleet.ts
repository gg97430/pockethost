/** Read-only cross-host DTO. No native records or remote-provided URLs cross this boundary. */
export const fleetPeers = ['app3', 'app4', 'app5'] as const
export type FleetPeer = (typeof fleetPeers)[number]
export type FleetAccess = 'granted' | 'denied' | 'unavailable' | 'notConfigured'
export type FleetMetrics = {
  collectedAt: string | null
  stale: boolean
  cpuPercent: number | null
  memoryTotalBytes: number | null
  memoryUsedBytes: number | null
  diskTotalBytes: number | null
  diskUsedBytes: number | null
}
export type FleetResource = {
  id: string
  kind: 'database' | 'application'
  name: string
  status: string
  cpuPercent: number | null
  memoryBytes: number | null
  diskBytes: number | null
}
export type FleetServer = {
  key: FleetPeer
  hostname: string
  access: FleetAccess
  checkedAt: string
  metrics?: FleetMetrics | null
  monitoring?: {
    checkedAt: string | null
    stale: boolean
    state: 'ok' | 'warning' | 'error' | 'unknown' | 'paused'
    counts: Record<'ok' | 'warning' | 'error' | 'unknown' | 'paused', number>
  } | null
  counts?: {
    databases: number
    applications: number
    databasesRunning: number
    applicationsRunning: number
    deploymentsRunning: number
    deploymentsQueued: number
    deploymentsFailed24h: number
  }
  inventory?: FleetResource[]
  truncated?: boolean
}
export type PlatformFleet = { version: 1; checkedAt: string; self: FleetPeer; servers: FleetServer[] }

const invalid = (): never => {
  throw Error('Rapport de flotte invalide.')
}
const obj = (value: unknown): Record<string, any> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, any>) : invalid()
const text = (value: unknown, max: number) => (typeof value === 'string' && value.length <= max ? value : invalid())
const date = (value: unknown): string => {
  const result = text(value, 40)
  return Number.isFinite(Date.parse(result)) ? new Date(result).toISOString() : invalid()
}
const number = (value: unknown, max = Number.MAX_SAFE_INTEGER) =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= max ? value : invalid()
const count = (value: unknown) => {
  const n = number(value, Number.MAX_SAFE_INTEGER)
  return Number.isInteger(n) ? n : invalid()
}
const optionalNumber = (value: unknown, max = Number.MAX_SAFE_INTEGER) => (value === null ? null : number(value, max))
const states = ['ok', 'warning', 'error', 'unknown', 'paused'] as const
const stale = (value: string | null, age: number, now: number) =>
  value === null || now - Date.parse(value) > age || Date.parse(value) > now + 30000

/** Reconstruct every field; peer identity, links, counts and freshness are never accepted blindly. */
export function parsePlatformFleet(value: unknown, now = Date.now()): PlatformFleet {
  const raw = obj(value)
  if (raw.version !== 1 || !fleetPeers.includes(raw.self) || !Array.isArray(raw.servers) || raw.servers.length !== 3)
    return invalid()
  const servers = raw.servers.map((input: unknown): FleetServer => {
    const row = obj(input)
    if (!fleetPeers.includes(row.key)) return invalid()
    const unavailable: FleetServer = {
      key: row.key,
      hostname: `${row.key}.monappli.re`,
      access: 'unavailable',
      checkedAt: new Date(now).toISOString(),
    }
    try {
      if (
        row.hostname !== `${row.key}.monappli.re` ||
        !['granted', 'denied', 'unavailable', 'notConfigured'].includes(row.access)
      )
        return invalid()
      const server: FleetServer = {
        key: row.key,
        hostname: `${row.key}.monappli.re`,
        access: row.access,
        checkedAt: date(row.checkedAt),
      }
      if (row.access !== 'granted') return server
      const counts = obj(row.counts)
      server.counts = {
        databases: count(counts.databases),
        applications: count(counts.applications),
        databasesRunning: count(counts.databasesRunning),
        applicationsRunning: count(counts.applicationsRunning),
        deploymentsRunning: count(counts.deploymentsRunning),
        deploymentsQueued: count(counts.deploymentsQueued),
        deploymentsFailed24h: count(counts.deploymentsFailed24h),
      }
      if (!Array.isArray(row.inventory) || row.inventory.length > 2000 || typeof row.truncated !== 'boolean')
        return invalid()
      server.inventory = row.inventory.map((value: unknown): FleetResource => {
        const item = obj(value),
          id = text(item.id, 15)
        if (!/^[a-z0-9]{15}$/.test(id) || !['database', 'application'].includes(item.kind)) return invalid()
        return {
          id,
          kind: item.kind,
          name: text(item.name, 200),
          status: text(item.status, 40),
          cpuPercent: optionalNumber(item.cpuPercent, 100),
          memoryBytes: optionalNumber(item.memoryBytes),
          diskBytes: optionalNumber(item.diskBytes),
        }
      })
      if (new Set(server.inventory.map((item) => `${item.kind}:${item.id}`)).size !== server.inventory.length)
        return invalid()
      server.truncated = row.truncated
      server.metrics = null
      if (row.metrics !== null) {
        const metrics = obj(row.metrics),
          collectedAt = metrics.collectedAt === null ? null : date(metrics.collectedAt)
        server.metrics = {
          collectedAt,
          stale: metrics.stale === true || stale(collectedAt, 60000, now),
          cpuPercent: optionalNumber(metrics.cpuPercent, 100),
          memoryTotalBytes: optionalNumber(metrics.memoryTotalBytes),
          memoryUsedBytes: optionalNumber(metrics.memoryUsedBytes),
          diskTotalBytes: optionalNumber(metrics.diskTotalBytes),
          diskUsedBytes: optionalNumber(metrics.diskUsedBytes),
        }
      }
      server.monitoring = null
      if (row.monitoring !== null) {
        const monitor = obj(row.monitoring),
          counts = obj(monitor.counts),
          checkedAt = monitor.checkedAt === null ? null : date(monitor.checkedAt)
        const clean = Object.fromEntries(states.map((state) => [state, count(counts[state])])) as NonNullable<
          FleetServer['monitoring']
        >['counts']
        server.monitoring = {
          checkedAt,
          stale: monitor.stale === true || stale(checkedAt, 180000, now),
          counts: clean,
          state: (['error', 'warning', 'unknown'] as const).find((state) => clean[state] > 0) || 'ok',
        }
      }
      return server
    } catch {
      // A corrupt peer cannot turn its own partial data into a green result,
      // nor prevent reading the other two healthy peers.
      return unavailable
    }
  })
  if (new Set(servers.map((row) => row.key)).size !== 3) return invalid()
  return {
    version: 1,
    checkedAt: date(raw.checkedAt),
    self: raw.self,
    servers: fleetPeers.map((key) => servers.find((row) => row.key === key)!),
  }
}
