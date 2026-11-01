import { describe, expect, it } from 'vitest'
import { emptyPlatformResources, parsePlatformResources, PLATFORM_RESOURCE_CATEGORIES } from './platformResources'

const now = Date.parse('2026-10-31T13:00:00Z')
const metric = () => ({ cpuPercent: 0, memoryBytes: 0, diskBytes: null })
const fixture = () => ({
  available: true,
  stale: false,
  range: '1h',
  sample: {
    version: 1,
    collectedAt: new Date(now).toISOString(),
    diskCollectedAt: null,
    diskAttribution: 'directory-estimate',
    host: {
      hostname: 'fixture',
      uptimeSeconds: 10,
      cpuCount: 12,
      cpuPercent: 8.3,
      memoryTotalBytes: 1000,
      memoryUsedBytes: 200,
      memoryAvailableBytes: 800,
      diskTotalBytes: 10000,
      diskUsedBytes: 3000,
      diskAvailableBytes: 7000,
    },
    categories: PLATFORM_RESOURCE_CATEGORIES.map((id) => ({ id, resourceCount: 1, ...metric() })),
    resources: [
      {
        id: 'a'.repeat(15),
        kind: 'application',
        category: 'hosting',
        state: 'running',
        memoryLimitBytes: 768,
        ...metric(),
      },
    ],
    filesystems: [{ mount: '/', totalBytes: 10000, usedBytes: 3000, availableBytes: 7000, usedPercent: 30 }],
    errors: [],
  },
  history: {
    points: [
      {
        timestamp: now,
        ...metric(),
        categories: Object.fromEntries(PLATFORM_RESOURCE_CATEGORIES.map((id) => [id, metric()])),
      },
    ],
    firstCollectedAt: new Date(now).toISOString(),
    intervalSeconds: 15,
    retentionDays: 7,
  },
})

describe('operator global resource metrics', () => {
  it('preserves real zero measurements and unknown values separately', () => {
    const response = parsePlatformResources(fixture(), '1h', now)
    expect(response.available).toBe(true)
    expect(response.stale).toBe(false)
    expect(response.sample?.resources[0]).toMatchObject({ cpuPercent: 0, memoryBytes: 0, diskBytes: null })
    expect(response.sample?.host.cpuCount).toBe(12)
    expect(response.history.points[0]?.timestamp).toBe(now)
  })
  it('uses empty, explicitly unavailable data before the first collection', () => {
    expect(parsePlatformResources({ range: '1h', available: false, sample: null }, '1h', now)).toEqual(
      emptyPlatformResources('1h')
    )
  })
  it('calculates stale and future status rather than trusting a green cache', () => {
    for (const offset of [-60001, 30001]) {
      const input = fixture()
      input.sample.collectedAt = new Date(now + offset).toISOString()
      expect(parsePlatformResources(input, '1h', now).stale).toBe(true)
    }
  })
  it('drops unknown fields, collector labels and unsafe links at the private file boundary', () => {
    const input = fixture()
    Object.assign(input, { token: 'never-public' })
    Object.assign(input.sample.host, { password: 'never-public' })
    Object.assign(input.sample.resources[0]!, {
      env: 'never-public',
      name: 'never-public',
      href: 'https://outside.invalid',
    })
    expect(JSON.stringify(parsePlatformResources(input, '1h', now))).not.toContain('never-public')
    expect(JSON.stringify(parsePlatformResources(input, '1h', now))).not.toContain('outside.invalid')
  })
  it('rejects invalid numeric measurements and percentages beyond host capacity', () => {
    for (const value of [-1, Number.NaN, Number.POSITIVE_INFINITY, 101]) {
      const input = fixture()
      input.sample.host.cpuPercent = value
      expect(() => parsePlatformResources(input, '1h', now)).toThrow()
    }
  })
  it('rejects duplicate categories and unbounded resource lists', () => {
    const duplicate = fixture()
    duplicate.sample.categories[1] = duplicate.sample.categories[0]!
    expect(() => parsePlatformResources(duplicate, '1h', now)).toThrow()
    const missing = fixture()
    missing.sample.categories.pop()
    expect(() => parsePlatformResources(missing, '1h', now)).toThrow()
    const duplicateRows = fixture()
    duplicateRows.sample.resources.push(duplicateRows.sample.resources[0]!)
    expect(() => parsePlatformResources(duplicateRows, '1h', now)).toThrow()
  })
  it('rejects resource and filesystem path traversal', () => {
    const input = fixture()
    input.sample.resources[0]!.id = '../../private'
    expect(() => parsePlatformResources(input, '1h', now)).toThrow()
    const mount = fixture()
    mount.sample.filesystems[0]!.mount = '/boot/../private'
    expect(() => parsePlatformResources(mount, '1h', now)).toThrow()
  })
  it('requires ordered bounded history without silently manufacturing points', () => {
    const input = fixture()
    input.history.points.push(input.history.points[0]!)
    expect(() => parsePlatformResources(input, '1h', now)).toThrow()
    const oversized = fixture()
    oversized.history.points = Array.from({ length: 241 }, (_, index) => ({
      ...oversized.history.points[0]!,
      timestamp: now + index,
    }))
    expect(() => parsePlatformResources(oversized, '1h', now)).toThrow()
  })
  it('binds each cached view to the requested range', () => {
    expect(() => parsePlatformResources(fixture(), '7d', now)).toThrow()
  })
})
