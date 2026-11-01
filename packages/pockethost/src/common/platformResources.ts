/** Sanitized resource metrics shared by the local collector and operator console. */
export const PLATFORM_RESOURCE_CATEGORIES = ['databases', 'hosting', 'builds', 'platform', 'backups', 'system'] as const
export const PLATFORM_RESOURCE_RANGES = ['15m', '1h', '24h', '7d'] as const
export type PlatformResourceCategoryId = (typeof PLATFORM_RESOURCE_CATEGORIES)[number]
export type PlatformResourceRange = (typeof PLATFORM_RESOURCE_RANGES)[number]

export type PlatformResourceValues = {
  /** Percentage of the whole host: all logical CPUs together are 100%. */
  cpuPercent: number | null
  memoryBytes: number | null
  diskBytes: number | null
}
export type PlatformResourceCategory = PlatformResourceValues & {
  id: PlatformResourceCategoryId
  resourceCount: number
}
export type PlatformResourceRow = PlatformResourceValues & {
  id: string
  category: PlatformResourceCategoryId
  kind: 'database' | 'application' | 'builder' | 'service'
  state: 'running' | 'stopped' | 'unknown'
  memoryLimitBytes: number | null
  /** Labels and links are enriched by the authenticated controller. */
  name?: string
  href?: string
}
export type PlatformFilesystem = {
  mount: string
  totalBytes: number
  usedBytes: number
  availableBytes: number
  usedPercent: number
}
export type PlatformResourceHost = {
  hostname: string
  uptimeSeconds: number
  cpuCount: number
  cpuPercent: number | null
  memoryTotalBytes: number
  memoryUsedBytes: number
  memoryAvailableBytes: number
  diskTotalBytes: number
  diskUsedBytes: number
  diskAvailableBytes: number
}
export type PlatformResourceSample = {
  version: 1
  collectedAt: string
  diskCollectedAt: string | null
  diskAttribution: 'directory-estimate'
  host: PlatformResourceHost
  categories: PlatformResourceCategory[]
  resources: PlatformResourceRow[]
  filesystems: PlatformFilesystem[]
  /** Fixed diagnostic codes only; never paths, credentials or command output. */
  errors: string[]
}
export type PlatformResourceHistoryPoint = {
  timestamp: number
  cpuPercent: number | null
  memoryBytes: number | null
  diskBytes: number | null
  categories: Record<PlatformResourceCategoryId, PlatformResourceValues>
}
export type PlatformResourceHistory = {
  points: PlatformResourceHistoryPoint[]
  firstCollectedAt: string | null
  intervalSeconds: number
  retentionDays: number
}
export type PlatformResourceResponse = {
  available: boolean
  stale: boolean
  range: PlatformResourceRange
  sample: PlatformResourceSample | null
  history: PlatformResourceHistory
}

const invalid = (): never => {
  throw Error('Mesures de ressources invalides.')
}
const object = (value: unknown): Record<string, any> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, any>) : invalid()
const number = (value: unknown, maximum = Number.MAX_SAFE_INTEGER): number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= maximum ? value : invalid()
const optionalNumber = (value: unknown, maximum = Number.MAX_SAFE_INTEGER) =>
  value === null ? null : number(value, maximum)
const date = (value: unknown): string => {
  if (typeof value !== 'string' || value.length > 40 || !Number.isFinite(Date.parse(value))) return invalid()
  return new Date(value).toISOString()
}
const text = (value: unknown, maximum: number): string =>
  typeof value === 'string' && value.length <= maximum ? value : invalid()
const values = (value: unknown): PlatformResourceValues => {
  const raw = object(value)
  return {
    cpuPercent: optionalNumber(raw.cpuPercent, 100),
    memoryBytes: optionalNumber(raw.memoryBytes),
    diskBytes: optionalNumber(raw.diskBytes),
  }
}
const categoryId = (value: unknown): PlatformResourceCategoryId =>
  PLATFORM_RESOURCE_CATEGORIES.includes(value as PlatformResourceCategoryId)
    ? (value as PlatformResourceCategoryId)
    : invalid()
const array = (value: unknown, maximum: number): any[] =>
  Array.isArray(value) && value.length <= maximum ? value : invalid()

export const emptyPlatformResources = (range: PlatformResourceRange): PlatformResourceResponse => ({
  available: false,
  stale: true,
  range,
  sample: null,
  history: { points: [], firstCollectedAt: null, intervalSeconds: 15, retentionDays: 7 },
})

/** Only explicit non-secret fields cross the privileged collector → operator API boundary. */
export function parsePlatformResources(
  value: unknown,
  range: PlatformResourceRange,
  now = Date.now()
): PlatformResourceResponse {
  const raw = object(value)
  if (raw.range !== range || typeof raw.available !== 'boolean') return invalid()
  if (!raw.available || raw.sample === null) return emptyPlatformResources(range)
  const source = object(raw.sample),
    host = object(source.host)
  if (source.version !== 1 || source.diskAttribution !== 'directory-estimate') return invalid()
  const cpuCount = number(host.cpuCount, 65536)
  if (!Number.isInteger(cpuCount) || cpuCount < 1) return invalid()
  const categories = array(source.categories, 6).map((item): PlatformResourceCategory => {
    const row = object(item),
      resourceCount = number(row.resourceCount, 100000)
    if (!Number.isInteger(resourceCount)) return invalid()
    return { id: categoryId(row.id), resourceCount, ...values(row) }
  })
  if (categories.length !== 6 || new Set(categories.map((row) => row.id)).size !== 6) return invalid()
  const resources = array(source.resources, 4096).map((item): PlatformResourceRow => {
    const row = object(item),
      id = text(row.id, 160)
    if (
      !/^[A-Za-z0-9:_@.-]{1,160}$/.test(id) ||
      !['database', 'application', 'builder', 'service'].includes(row.kind) ||
      !['running', 'stopped', 'unknown'].includes(row.state)
    )
      return invalid()
    return {
      id,
      category: categoryId(row.category),
      kind: row.kind,
      state: row.state,
      memoryLimitBytes: optionalNumber(row.memoryLimitBytes),
      ...values(row),
    }
  })
  if (new Set(resources.map((row) => `${row.category}:${row.id}`)).size !== resources.length) return invalid()
  const sample: PlatformResourceSample = {
    version: 1,
    collectedAt: date(source.collectedAt),
    diskCollectedAt: source.diskCollectedAt === null ? null : date(source.diskCollectedAt),
    diskAttribution: 'directory-estimate',
    host: {
      hostname: text(host.hostname, 200),
      uptimeSeconds: number(host.uptimeSeconds),
      cpuCount,
      cpuPercent: optionalNumber(host.cpuPercent, 100),
      memoryTotalBytes: number(host.memoryTotalBytes),
      memoryUsedBytes: number(host.memoryUsedBytes),
      memoryAvailableBytes: number(host.memoryAvailableBytes),
      diskTotalBytes: number(host.diskTotalBytes),
      diskUsedBytes: number(host.diskUsedBytes),
      diskAvailableBytes: number(host.diskAvailableBytes),
    },
    categories,
    resources,
    filesystems: array(source.filesystems, 64).map((item): PlatformFilesystem => {
      const row = object(item),
        mount = text(row.mount, 255)
      if (!/^\/[A-Za-z0-9_./-]*$/.test(mount) || mount.split('/').includes('..')) return invalid()
      return {
        mount,
        totalBytes: number(row.totalBytes),
        usedBytes: number(row.usedBytes),
        availableBytes: number(row.availableBytes),
        usedPercent: number(row.usedPercent, 100),
      }
    }),
    errors: array(source.errors, 64).map((item) => {
      const code = text(item, 100)
      return /^[A-Za-z0-9:_-]+$/.test(code) ? code : invalid()
    }),
  }
  const history = object(raw.history),
    points = array(history.points, 240).map((item): PlatformResourceHistoryPoint => {
      const point = object(item),
        groups = object(point.categories),
        timestamp = number(point.timestamp, 8640000000000000)
      if (!Number.isInteger(timestamp)) return invalid()
      return {
        timestamp,
        ...values(point),
        categories: Object.fromEntries(PLATFORM_RESOURCE_CATEGORIES.map((id) => [id, values(groups[id])])) as Record<
          PlatformResourceCategoryId,
          PlatformResourceValues
        >,
      }
    })
  for (let index = 1; index < points.length; index++)
    if (points[index]!.timestamp <= points[index - 1]!.timestamp) return invalid()
  const intervalSeconds = number(history.intervalSeconds, 86400),
    retentionDays = number(history.retentionDays, 31)
  if (intervalSeconds < 1 || retentionDays < 1) return invalid()
  const age = now - Date.parse(sample.collectedAt)
  return {
    available: true,
    stale: raw.stale === true || age < -30000 || age > 60000,
    range,
    sample,
    history: {
      points,
      firstCollectedAt: history.firstCollectedAt === null ? null : date(history.firstCollectedAt),
      intervalSeconds,
      retentionDays,
    },
  }
}
