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
