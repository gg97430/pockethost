import type {
  PlatformResourceCategoryId,
  PlatformResourceHistoryPoint,
  PlatformResourceRange,
  PlatformResourceValues,
} from '../../../pockethost/src/common/platformResources'
export type * from '../../../pockethost/src/common/platformResources'

export const RESOURCE_CATEGORIES: { id: PlatformResourceCategoryId; label: string; color: string }[] = [
  { id: 'hosting', label: 'Hébergement', color: '#7297ff' },
  { id: 'databases', label: 'Bases de données', color: '#51d4b1' },
  { id: 'builds', label: 'Builds', color: '#f2bf63' },
  { id: 'platform', label: 'Plateforme', color: '#b9a0eb' },
  { id: 'backups', label: 'Sauvegardes', color: '#e89a7b' },
  { id: 'system', label: 'Système', color: '#a1afbf' },
]
export const RESOURCE_RANGES: { id: PlatformResourceRange; label: string; duration: number }[] = [
  { id: '15m', label: '15 min', duration: 15 * 60_000 },
  { id: '1h', label: '1 h', duration: 60 * 60_000 },
  { id: '24h', label: '24 h', duration: 24 * 60 * 60_000 },
  { id: '7d', label: '7 jours', duration: 7 * 24 * 60 * 60_000 },
]
export type ResourceMetric = keyof PlatformResourceValues
export type ResourceSeries = { id: string; label: string; color: string; category?: PlatformResourceCategoryId }
export const HOST_SERIES: ResourceSeries = {
  id: 'host',
  label: 'Serveur entier',
  color: 'var(--app-text-strong, #edf0f5)',
}
export const RESOURCE_GIB = 1024 ** 3
export const resourceNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
export const resourcePercent = (used: unknown, total: unknown) => {
  const value = resourceNumber(used),
    capacity = resourceNumber(total)
  return value !== null && capacity !== null && capacity > 0 ? (value / capacity) * 100 : null
}
const number = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 })
export const resourceFormatPercent = (value: unknown) => {
  const finite = resourceNumber(value)
  return finite === null ? '—' : `${number.format(finite)} %`
}
export const resourceFormatBytes = (value: unknown) => {
  const finite = resourceNumber(value)
  if (finite === null) return '—'
  const units = ['o', 'Ko', 'Mo', 'Go', 'To']
  let amount = finite,
    unit = 0
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit++
  }
  return `${number.format(amount)} ${units[unit]}`
}
export const resourceFormatDate = (value: string | number | null | undefined) => {
  const timestamp = typeof value === 'number' ? value : Date.parse(value || '')
  return Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleString('fr-FR', {
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : 'Aucune mesure'
}
export const resourceValue = (point: PlatformResourceHistoryPoint, series: ResourceSeries, metric: ResourceMetric) =>
  resourceNumber(series.category ? point.categories?.[series.category]?.[metric] : point[metric])
export const resourceHistory = (points: PlatformResourceHistoryPoint[], start: number, end: number) =>
  [
    ...new Map(
      points
        .filter((p) => Number.isFinite(p.timestamp) && p.timestamp >= start && p.timestamp <= end)
        .map((p) => [p.timestamp, p])
    ).values(),
  ].sort((a, b) => a.timestamp - b.timestamp)

/** Split at missing readings and collection gaps; never interpolate missing history. */
export const resourceSegments = (
  points: PlatformResourceHistoryPoint[],
  series: ResourceSeries,
  metric: ResourceMetric,
  start: number,
  end: number,
  maximum: number,
  intervalSeconds: number
) => {
  const segments: { x: number; y: number }[][] = []
  let current: { x: number; y: number }[] = [],
    previous: number | null = null
  const divisor = metric === 'cpuPercent' ? 1 : RESOURCE_GIB
  for (const point of points) {
    const value = resourceValue(point, series, metric)
    if (
      value === null ||
      (previous !== null && intervalSeconds > 0 && point.timestamp - previous > intervalSeconds * 2500)
    ) {
      if (current.length) segments.push(current)
      current = []
    }
    if (value !== null) {
      current.push({
        x: 54 + ((point.timestamp - start) / Math.max(1, end - start)) * 642,
        y: 20 + (1 - Math.max(0, Math.min(1, value / divisor / maximum))) * 160,
      })
    }
    previous = point.timestamp
  }
  if (current.length) segments.push(current)
  return segments
}
export const resourceMetricLabel = (metric: ResourceMetric) =>
  metric === 'cpuPercent' ? 'CPU' : metric === 'memoryBytes' ? 'RAM' : 'Disque'
export const resourceMetricFormat = (metric: ResourceMetric, value: unknown) =>
  metric === 'cpuPercent' ? resourceFormatPercent(value) : resourceFormatBytes(value)
export const resourceHref = (href: string | undefined) =>
  href && /^\/(applications|instances)\/[a-z0-9]{15}(?:[/?#]|$)/.test(href) ? href : undefined

export type ResourceSortDirection = 'asc' | 'desc'
const resourceCollator = new Intl.Collator('fr', { numeric: true, sensitivity: 'base' })
/** Stable in either direction, with unavailable values always after measured values. */
export function resourceSortRows<T>(
  rows: readonly T[],
  value: (row: T) => string | number | null | undefined,
  direction: ResourceSortDirection
): T[] {
  const key = (row: T) => {
    const v = value(row)
    return v === undefined || (typeof v === 'number' && !Number.isFinite(v)) ? null : v
  }
  return rows
    .map((row, index) => ({ row, index, value: key(row) }))
    .sort((a, b) => {
      if (a.value === null || b.value === null)
        return a.value === b.value ? a.index - b.index : a.value === null ? 1 : -1
      const difference =
        typeof a.value === 'number' && typeof b.value === 'number'
          ? a.value - b.value
          : resourceCollator.compare(String(a.value), String(b.value))
      return (direction === 'asc' ? difference : -difference) || a.index - b.index
    })
    .map((item) => item.row)
}
export const resourceSortAria = (
  key: string,
  current: string,
  direction: ResourceSortDirection
): 'ascending' | 'descending' | 'none' => (key !== current ? 'none' : direction === 'asc' ? 'ascending' : 'descending')
export const resourceSortIndicator = (key: string, current: string, direction: ResourceSortDirection) =>
  key !== current ? '↕' : direction === 'asc' ? '↑' : '↓'
