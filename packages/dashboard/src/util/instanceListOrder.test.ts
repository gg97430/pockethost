import type { InstanceFields } from 'pockethost/common'
import { describe, expect, it } from 'vitest'
import type { DashboardInstanceMetric } from '../pocketbase-client/PocketbaseClient'
import { compareInstanceList } from './instanceListOrder'
import { loadInstanceListPrefsFromUrl } from './instanceListPrefs'

const a = {
  id: 'a',
  subdomain: 'base2',
  status: 'running',
  power: true,
  version: '0.9.0',
  projectLabel: 'LAPUB',
} as InstanceFields
const b = {
  id: 'b',
  subdomain: 'base10',
  status: 'idle',
  power: false,
  version: '0.10.0',
  projectLabel: '',
} as InstanceFields
const metric = (cpuPercent: number | null, memoryBytes: number, diskBytes: number) =>
  ({ cpuPercent, memoryBytes, diskBytes }) as DashboardInstanceMetric
const metrics = { a: metric(120, 1024, 10), b: metric(9, 4096, 30) }
describe('database list ordering', () => {
  it('compares numeric versions and names naturally, and reverses each criterion', () => {
    for (const column of ['name', 'version'] as const) {
      expect(compareInstanceList(a, b, column, 'asc', metrics)).toBeLessThan(0)
      expect(compareInstanceList(a, b, column, 'desc', metrics)).toBeGreaterThan(0)
    }
  })
  it('uses actual CPU and byte measurements instead of formatted strings', () => {
    expect(compareInstanceList(a, b, 'cpu', 'desc', metrics)).toBeLessThan(0)
    expect(compareInstanceList(a, b, 'memory', 'desc', metrics)).toBeGreaterThan(0)
    expect(compareInstanceList(a, b, 'disk', 'asc', metrics)).toBeLessThan(0)
  })
  it('keeps missing or invalid telemetry last in both directions, without confusing zero with missing', () => {
    for (const direction of ['asc', 'desc'] as const) {
      for (const value of [null, NaN, Infinity]) {
        expect(
          compareInstanceList(a, b, 'cpu', direction, { a: metric(value, 0, 0), b: metric(0, 0, 0) })
        ).toBeGreaterThan(0)
      }
      expect(compareInstanceList(a, b, 'memory', direction, { b: metrics.b })).toBeGreaterThan(0)
    }
  })
  it('sorts power states and labels independently of instance names', () => {
    expect(compareInstanceList(a, b, 'power', 'desc', metrics)).toBeLessThan(0)
    expect(compareInstanceList(a, b, 'label', 'asc', metrics)).toBeLessThan(0)
    expect(compareInstanceList(a, b, 'label', 'desc', metrics)).toBeLessThan(0)
  })
  it('restores a shareable group and ordering, and ignores invalid preferences', () => {
    expect(loadInstanceListPrefsFromUrl(new URLSearchParams('by=memory&sort=desc&group=label%3ALAPUB'))).toMatchObject({
      sortBy: 'memory',
      sortDirection: 'desc',
      groupFilter: 'label:LAPUB',
    })
    expect(loadInstanceListPrefsFromUrl(new URLSearchParams('by=invalid&sort=wrong&group=invalid'))).toMatchObject({
      sortBy: 'favorites',
      sortDirection: 'asc',
      groupFilter: 'all',
    })
  })
})
