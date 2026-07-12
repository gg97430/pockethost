import type { DashboardInstanceMetric } from '$src/pocketbase-client'

export const INSTANCE_METRIC_POLL_INTERVAL_MS = 30_000
export const INSTANCE_METRIC_HISTORY_WINDOW_MS = 30 * 60_000
export const INSTANCE_METRIC_MAX_POINTS = INSTANCE_METRIC_HISTORY_WINDOW_MS / INSTANCE_METRIC_POLL_INTERVAL_MS + 1

export type InstanceMetricHistoryPoint = Pick<
  DashboardInstanceMetric,
  'cpuPercent' | 'memoryBytes' | 'memoryLimitBytes' | 'memoryPercent'
> & {
  timestamp: number
}
const finiteOrNull = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null)

export const toMetricHistoryPoint = (
  metric: DashboardInstanceMetric,
  collectedAt: string,
  fallbackTimestamp = Date.now()
): InstanceMetricHistoryPoint => {
  const parsedTimestamp = Date.parse(collectedAt)

  return {
    timestamp: Number.isFinite(parsedTimestamp) ? parsedTimestamp : fallbackTimestamp,
    cpuPercent: finiteOrNull(metric.cpuPercent),
    memoryBytes: finiteOrNull(metric.memoryBytes),
    memoryLimitBytes: finiteOrNull(metric.memoryLimitBytes),
    memoryPercent: finiteOrNull(metric.memoryPercent),
  }
}

const isHistoryPoint = (value: unknown): value is InstanceMetricHistoryPoint => {
  if (!value || typeof value !== 'object') return false
  const point = value as Record<string, unknown>
  return typeof point.timestamp === 'number' && Number.isFinite(point.timestamp)
}

export const pruneMetricHistory = (
  points: InstanceMetricHistoryPoint[],
  now = Date.now()
): InstanceMetricHistoryPoint[] => {
  const oldestTimestamp = now - INSTANCE_METRIC_HISTORY_WINDOW_MS
  return points
    .filter((point) => point.timestamp >= oldestTimestamp && point.timestamp <= now + INSTANCE_METRIC_POLL_INTERVAL_MS)
    .sort((a, b) => a.timestamp - b.timestamp)
    .slice(-INSTANCE_METRIC_MAX_POINTS)
}

export const appendMetricHistory = (
  points: InstanceMetricHistoryPoint[],
  point: InstanceMetricHistoryPoint,
  now = Date.now()
) => pruneMetricHistory([...points.filter((entry) => entry.timestamp !== point.timestamp), point], now)

export const parseMetricHistory = (value: string | null, now = Date.now()): InstanceMetricHistoryPoint[] => {
  if (!value) return []

  try {
    const parsed = JSON.parse(value)
    if (!Array.isArray(parsed)) return []
    return pruneMetricHistory(parsed.filter(isHistoryPoint), now).map((point) => ({
      timestamp: point.timestamp,
      cpuPercent: finiteOrNull(point.cpuPercent),
      memoryBytes: finiteOrNull(point.memoryBytes),
      memoryLimitBytes: finiteOrNull(point.memoryLimitBytes),
      memoryPercent: finiteOrNull(point.memoryPercent),
    }))
  } catch {
    return []
  }
}
