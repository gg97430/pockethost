import type { DashboardInstanceMetric } from '$src/pocketbase-client'
import { describe, expect, it } from 'vitest'
import {
  INSTANCE_METRIC_HISTORY_WINDOW_MS,
  INSTANCE_METRIC_MAX_POINTS,
  appendMetricHistory,
  parseMetricHistory,
  pruneMetricHistory,
  toMetricHistoryPoint,
  type InstanceMetricHistoryPoint,
} from './instanceMetricHistory'

const metric: DashboardInstanceMetric = {
  instanceId: 'instance1',
  cpuPercent: 12.5,
  cpuCoresUsed: 0.125,
  cpuAvailableCores: 8,
  cpuHostCores: 8,
  cpuCapacityPercent: 1.5625,
  memoryBytes: 256,
  memoryLimitBytes: 1024,
  memoryPercent: 25,
  diskBytes: null,
  blockReadBytes: null,
  blockWriteBytes: null,
  containerName: 'instance1',
}

describe('instance metric history', () => {
  it('converts an API metric into a compact history point', () => {
    expect(toMetricHistoryPoint(metric, '2026-07-12T10:00:00.000Z')).toEqual({
      timestamp: Date.parse('2026-07-12T10:00:00.000Z'),
      cpuPercent: 12.5,
      cpuCoresUsed: 0.125,
      cpuAvailableCores: 8,
      cpuHostCores: 8,
      cpuCapacityPercent: 1.5625,
      memoryBytes: 256,
      memoryLimitBytes: 1024,
      memoryPercent: 25,
    })
  })

  it('keeps only the rolling 30 minute window', () => {
    const now = 2_000_000
    const points: InstanceMetricHistoryPoint[] = [
      { ...toMetricHistoryPoint(metric, '', now), timestamp: now - INSTANCE_METRIC_HISTORY_WINDOW_MS - 1 },
      { ...toMetricHistoryPoint(metric, '', now), timestamp: now - 1_000 },
    ]

    expect(pruneMetricHistory(points, now)).toHaveLength(1)
  })

  it('preserves CPU percentages above one core without capping them at 100%', () => {
    const multiCoreMetric: DashboardInstanceMetric = {
      ...metric,
      cpuPercent: 598.45,
      cpuCoresUsed: 5.9845,
      cpuCapacityPercent: 74.80625,
    }

    const point = toMetricHistoryPoint(multiCoreMetric, '2026-07-12T10:00:00.000Z')

    expect(point.cpuPercent).toBe(598.45)
    expect(point.cpuCoresUsed).toBe(5.9845)
    expect(point.cpuCapacityPercent).toBe(74.80625)
  })

  it('replaces duplicate timestamps and caps the number of samples', () => {
    const now = 10_000_000
    const points = Array.from({ length: INSTANCE_METRIC_MAX_POINTS }, (_, index) => ({
      ...toMetricHistoryPoint(metric, '', now),
      timestamp: now - (INSTANCE_METRIC_MAX_POINTS - index) * 1_000,
    }))
    const duplicate = { ...points.at(-1)!, cpuPercent: 88 }
    const replaced = appendMetricHistory(points, duplicate, now)

    expect(replaced).toHaveLength(INSTANCE_METRIC_MAX_POINTS)
    expect(replaced.at(-1)?.cpuPercent).toBe(88)
  })

  it('ignores malformed session storage values', () => {
    expect(parseMetricHistory('{broken')).toEqual([])
    expect(parseMetricHistory(JSON.stringify([{ timestamp: 'nope' }]))).toEqual([])
  })
})
